#!/usr/bin/env python3
"""
medium_clap.py — Daily script to clap on Medium subscriber notification emails.

Fetches unread Medium subscription emails from the last 24 hours via Gmail API
(deathtomosttyrants@gmail.com), navigates to each post in a logged-in Playwright
browser, and clicks the Clap button a random number of times (10–30).
Marks successfully clapped emails as read. Failed ones are left unread.

First run: opens a visible browser so you can log in to Medium. Subsequent runs
use the saved session headlessly.
"""

import glob
import json
import os
import re
import base64
import time
import random
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Reuse the same OAuth client credentials; separate token for the Medium Gmail account.
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "gmail_credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "medium_gmail_token.json")
PLAYWRIGHT_PROFILE = os.path.join(SCRIPT_DIR, "medium_playwright_profile")
CLAPPED_URLS_FILE = os.path.join(SCRIPT_DIR, "medium_clapped_urls.json")

CLAPS_MIN = 10
CLAPS_MAX = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Chromium ──────────────────────────────────────────────────────────────────

def get_chromium_executable() -> str | None:
    pattern = os.path.expanduser(
        "~/Library/Caches/ms-playwright/chromium-*/"
        "chrome-mac-*/Google Chrome for Testing.app/"
        "Contents/MacOS/Google Chrome for Testing"
    )
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


# ── Gmail helpers ─────────────────────────────────────────────────────────────

def get_gmail_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def get_email_body_html(msg_payload) -> str | None:
    if msg_payload.get("mimeType") == "text/html":
        data = msg_payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in msg_payload.get("parts", []):
        result = get_email_body_html(part)
        if result:
            return result
    return None


def find_post_url(html: str) -> str | None:
    """Return the post URL from the 'Continue reading' (or 'View on Medium') link.

    Strips tracking query parameters — keeps only the path.
    """
    soup = BeautifulSoup(html, "html.parser")

    target = None
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"]
        if text == "Continue reading":
            target = href
            break
        if text == "View on Medium" and target is None:
            target = href  # fallback; keep scanning for Continue reading

    if not target:
        return None

    # Strip ?source=... tracking params — the path alone is sufficient.
    parsed = urlparse(target)
    return urlunparse(parsed._replace(query="", fragment=""))


def mark_as_read(service, msg_id: str) -> None:
    # Retry once with a fresh service if the SSL/TCP connection went stale
    # during a long Playwright session (httplib2 doesn't recover on its own).
    try:
        service.users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
    except (TimeoutError, OSError) as e:
        log.warning(f"Gmail connection dropped, retrying with fresh service: {e}")
        get_gmail_service().users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()


def iter_messages(service, query: str):
    page_token = None
    while True:
        kwargs = {"userId": "me", "q": query}
        if page_token:
            kwargs["pageToken"] = page_token
        result = service.users().messages().list(**kwargs).execute()
        for msg_ref in result.get("messages", []):
            yield msg_ref
        page_token = result.get("nextPageToken")
        if not page_token:
            break


# ── Playwright helpers ────────────────────────────────────────────────────────

_BROWSER_ARGS = [
    "--password-store=basic",
    "--disable-blink-features=AutomationControlled",
]


def _launch(p, exe: str | None, headless: bool):
    return p.chromium.launch_persistent_context(
        PLAYWRIGHT_PROFILE,
        headless=headless,
        executable_path=exe,
        args=_BROWSER_ARGS,
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )


def check_medium_login(p, exe: str | None) -> bool:
    ctx = _launch(p, exe, headless=True)
    try:
        cookies = ctx.cookies(["https://medium.com"])
        # 'uid' is present for any signed-in Medium session.
        return any(c["name"] == "uid" for c in cookies)
    except Exception:
        return False
    finally:
        ctx.close()


def interactive_login(p, exe: str | None) -> None:
    log.info("Opening browser for Medium login...")
    ctx = _launch(p, exe, headless=False)
    try:
        page = ctx.new_page()
        page.goto("https://medium.com/m/signin", wait_until="domcontentloaded")
        input("\n  >>> Log in to Medium in the browser window, then press Enter here to continue. <<<\n")
    finally:
        ctx.close()
    log.info("Login session saved.")


def load_clapped_urls() -> set[str]:
    """Return the set of post URLs already clapped in previous runs."""
    if not os.path.exists(CLAPPED_URLS_FILE):
        return set()
    with open(CLAPPED_URLS_FILE) as f:
        return set(json.load(f).keys())


def record_clapped_url(url: str) -> None:
    """Persist a successfully clapped URL so future runs skip it."""
    data: dict = {}
    if os.path.exists(CLAPPED_URLS_FILE):
        with open(CLAPPED_URLS_FILE) as f:
            data = json.load(f)
    data[url] = datetime.now(timezone.utc).isoformat()
    with open(CLAPPED_URLS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def clap_post(page, post_url: str) -> bool:
    """Navigate to a post and click the Clap button a random number of times.

    Returns True if at least one clap was successfully registered, or if the
    post was already clapped (skipped cleanly).
    """
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        log.error(f"  Navigation failed: {e}")
        return False

    # Detect sign-in redirect
    if "/m/signin" in page.url or "/signin" in page.url:
        log.warning(f"  Redirected to sign-in, skipping: {post_url}")
        return False

    # Medium is a React SPA — wait for the article to hydrate beyond domcontentloaded.
    # The clap button lives in the article footer, which renders after the above event.
    time.sleep(3)

    # Try multiple selectors for the clap button — Medium's markup has changed over time.
    # We prefer the in-article footer button over any floating/header variant.
    clap_selectors = [
        'button[aria-label="clap"]',
        'button[aria-label="Clap"]',
        'button[aria-label*="clap" i]',
        'button[data-testid*="clap" i]',
    ]

    clap_btn = None
    matched_selector = None
    for selector in clap_selectors:
        try:
            page.wait_for_selector(selector, state="attached", timeout=5000)
            clap_btn = page.locator(selector).first
            matched_selector = selector
            break
        except Exception:
            continue

    if clap_btn is None:
        log.warning(f"  Clap button not found on {page.url}")
        return False

    log.info(f"  Found clap button via {matched_selector!r}")
    clap_btn.scroll_into_view_if_needed()

    n_claps = random.randint(CLAPS_MIN, CLAPS_MAX)
    log.info(f"  Clicking clap {n_claps} times...")

    clapped = 0
    for i in range(n_claps):
        try:
            clap_btn.click()
            clapped += 1
            time.sleep(0.15)
        except Exception as e:
            log.warning(f"  Clap click {i + 1} failed: {e}")
            break

    if clapped == 0:
        return False

    # Give the API a moment to register the last clap before we navigate away.
    time.sleep(1)
    log.info(f"  Clapped {clapped} times.")
    return True


def clap_all(
    p,
    exe: str | None,
    items: list[tuple[str, str, str]],
    gmail,
) -> tuple[int, int]:
    """Clap each post and mark the email read immediately on success.

    items is a list of (msg_id, post_url, label) triples.
    Returns (clapped_count, failed_count).
    Fresh page per post to avoid browser state accumulation.
    """
    clapped = failed = 0
    ctx = _launch(p, exe, headless=True)
    try:
        for msg_id, post_url, label in items:
            log.info(f"Processing: {label!r}")
            page = ctx.new_page()
            try:
                success = clap_post(page, post_url)
            finally:
                page.close()
            if success:
                mark_as_read(gmail, msg_id)
                record_clapped_url(post_url)
                log.info(f"  Clapped + marked read: {label!r}")
                clapped += 1
            else:
                log.warning(f"  Clap failed, leaving unread: {label!r}")
                failed += 1
            time.sleep(5)
    finally:
        ctx.close()
    return clapped, failed


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    gmail = get_gmail_service()

    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    after_epoch = int(cutoff.timestamp())
    query = f"from:subscriptions@medium.com after:{after_epoch}"
    log.info(f"Searching Gmail: {query}")

    already_clapped = load_clapped_urls()

    to_clap: list[tuple[str, str, str]] = []  # (msg_id, subject, post_url)
    skipped = 0

    for msg_ref in iter_messages(gmail, query):
        msg = gmail.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        subject = headers.get("Subject", "(no subject)")

        html = get_email_body_html(msg["payload"])
        if not html:
            log.warning(f"  No HTML body, skipping: {subject!r}")
            skipped += 1
            continue

        post_url = find_post_url(html)
        if not post_url:
            log.info(f"  No post URL found, skipping: {subject!r}")
            skipped += 1
            continue

        if post_url in already_clapped:
            log.info(f"  Already clapped, skipping: {subject!r}")
            mark_as_read(gmail, msg_ref["id"])
            skipped += 1
            continue

        log.info(f"Found: {subject!r} → {post_url}")
        to_clap.append((msg_ref["id"], subject, post_url))

    if not to_clap:
        log.info(f"No Medium emails to clap (skipped {skipped}).")
        return

    log.info(f"{len(to_clap)} email(s) to clap, {skipped} skipped. Opening Playwright...")

    exe = get_chromium_executable()
    if not exe:
        log.warning("Full Chromium not found; falling back to Playwright default (may fail on macOS).")

    with sync_playwright() as p:
        if not check_medium_login(p, exe):
            interactive_login(p, exe)
            if not check_medium_login(p, exe):
                log.error("Still not logged in after interactive login. Aborting.")
                return

        items = [(msg_id, post_url, subject) for msg_id, subject, post_url in to_clap]
        clapped, failed = clap_all(p, exe, items, gmail)

    log.info(f"Done. Clapped: {clapped}, Failed: {failed}, Skipped: {skipped}")


if __name__ == "__main__":
    main()
