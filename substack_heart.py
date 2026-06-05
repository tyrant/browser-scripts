#!/usr/bin/env python3
"""
substack_heart.py — Daily script to heart Substack subscriber notification emails.

Fetches unread Substack subscriber notification emails from the last 24 hours via
Gmail API, navigates to each post in a logged-in Playwright browser, clicks the
Like button (POST /api/v1/post/{id}/reaction — the same call the web UI makes),
then marks the email as read. Emails from no-reply@substack.com are skipped.

First run: opens a visible browser so you can log in to Substack. Subsequent
runs use the saved session headlessly.
"""

import glob
import os
import re
import sys
import base64
import time
import logging
import traceback
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from monitor_client import RunLogger, report_run

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "gmail_credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "gmail_token.json")
PLAYWRIGHT_PROFILE = os.path.join(SCRIPT_DIR, "playwright_profile")
RETRY_DELAY_SECS = 60  # wait before retrying failed likes (clears Substack 429 limits)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ── Chromium ──────────────────────────────────────────────────────────────────

def get_chromium_executable() -> str | None:
    """Return the full Chromium binary installed by Playwright (not headless-shell).

    On macOS the headless-shell uses a different cookie encryption path than the
    full browser, so we pin to the same binary for both headless and headful runs.
    """
    if sys.platform == "darwin":
        pattern = os.path.expanduser(
            "~/Library/Caches/ms-playwright/chromium-*/"
            "chrome-mac-*/Google Chrome for Testing.app/"
            "Contents/MacOS/Google Chrome for Testing"
        )
    else:
        pattern = os.path.expanduser(
            "~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome"
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
            try:
                creds.refresh(Request())
            except Exception as e:
                if "invalid_grant" in str(e):
                    raise RuntimeError(
                        "Gmail token revoked. Delete gmail_token.json, re-run locally to "
                        "re-auth, then rsync the new token to the server. "
                        "See SUBSTACK_HEART_README.md."
                    ) from e
                raise
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def is_subscriber_notification(sender: str) -> bool:
    lower = sender.lower()
    return "@substack.com" in lower and "no-reply@substack.com" not in lower


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


def find_heart_link(html: str) -> str | None:
    """Return the submitLike URL from the email — confirms this email has a Like button."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "submitLike=true" in href:
            return href
        if a.get_text(strip=True) == "Like" and "substack.com/app-link/post" in href:
            return href
    return None


def find_post_url(html: str) -> str | None:
    """Return the native post URL from the email HTML.

    Extracts the pub and slug from the open.substack.com/pub/{pub}/p/{slug} link,
    then constructs https://{pub}.substack.com/p/{slug} directly. This avoids the
    open.substack.com → native domain redirect chain that produces ?triedRedirect=true
    URLs and broken page states. The substack.sid session cookie is valid for all
    *.substack.com domains, so direct navigation always works.
    """
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.match(r"https://open\.substack\.com/pub/([^/]+)/p/([^/?]+)", href)
        if m:
            pub, slug = m.group(1), m.group(2)
            return f"https://{pub}.substack.com/p/{slug}"
    return None


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

_BROWSER_ARGS = ["--password-store=basic"]


def _launch(p, exe: str | None, headless: bool):
    return p.chromium.launch_persistent_context(
        PLAYWRIGHT_PROFILE,
        headless=headless,
        executable_path=exe,
        args=_BROWSER_ARGS,
    )


def check_substack_login(p, exe: str | None) -> bool:
    ctx = _launch(p, exe, headless=True)
    try:
        cookies = ctx.cookies(["https://substack.com"])
        return any(c["name"] == "substack.sid" for c in cookies)
    except Exception:
        return False
    finally:
        ctx.close()


def interactive_login(p, exe: str | None) -> None:
    if not os.environ.get("DISPLAY") and sys.platform != "darwin":
        raise RuntimeError(
            "No DISPLAY available — cannot open a browser for interactive login. "
            "Re-copy playwright_profile from a machine with a display."
        )
    log.info("Opening browser for Substack login...")
    ctx = _launch(p, exe, headless=False)
    try:
        page = ctx.new_page()
        page.goto("https://substack.com/sign-in", wait_until="domcontentloaded")
        input("\n  >>> Log in to Substack in the browser window, then press Enter here to continue. <<<\n")
    finally:
        ctx.close()
    log.info("Login session saved.")


def like_post(page, post_url: str) -> bool | None:
    """Navigate to a post and click the Like button. Returns True on success."""
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        log.error(f"  Navigation failed: {e}")
        return False

    # Detect sign-in redirect (paywalled or session expired for this subdomain)
    if "sign-in" in page.url:
        log.warning(f"  Redirected to sign-in (paywalled?), skipping: {post_url}")
        return False

    # Detect age verification redirect — cannot complete headlessly, skip permanently.
    if "age-verification-required" in page.url:
        log.info(f"  Age-gated, skipping: {post_url}")
        return None

    # Detect custom-domain redirect — substack.sid cookie is scoped to *.substack.com
    # and won't transfer, so likes on custom domains silently fail.
    landed_host = urlparse(page.url).hostname or ""
    if not (landed_host.endswith(".substack.com") or landed_host == "substack.com"):
        log.info(f"  Custom domain ({landed_host}), skipping — session cookie not transferable.")
        return False

    # Wait for the Like button to appear in the DOM.
    # Use state='attached' rather than default 'visible' — some Substack pages render
    # the button but Playwright's visibility check fails (overlay, CSS transform, etc.)
    # 20s timeout: video-first and other non-standard page formats hydrate React slower.
    try:
        page.wait_for_selector('button[aria-label^="Like"]', state='attached', timeout=20000)
    except Exception:
        log.warning(f"  Like button not found on {page.url}")
        return False

    like_btn = page.locator('button[aria-label^="Like"]').first

    # Already liked if aria-pressed="true".
    # Use a short timeout — the button was just found via wait_for_selector, but React
    # can re-render it between the two calls. Fail fast rather than hang for 30s.
    try:
        if like_btn.get_attribute("aria-pressed", timeout=5000) == "true":
            log.info("  Already liked.")
            return True
    except Exception:
        pass  # button detached mid-render; proceed to click

    # Scroll the button into view before clicking
    like_btn.scroll_into_view_if_needed()

    # Click and wait for the POST /reaction API call
    try:
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/reaction" in r.url,
            timeout=8000,
        ) as resp_info:
            like_btn.click()
        status = resp_info.value.status
        log.info(f"  Reaction API: {status}")
        if status == 429:
            log.warning("  Rate limited (429). Waiting 30s before next post.")
            time.sleep(30)
            return False
        return 200 <= status < 400
    except Exception:
        # No POST /reaction observed — check if the like registered anyway (different endpoint?)
        try:
            pressed = like_btn.get_attribute("aria-pressed")
            current_url = page.url
        except Exception:
            pressed, current_url = None, "unknown"
        log.warning(
            f"  No /reaction API captured — aria-pressed={pressed!r} url={current_url!r}"
        )
        # If the button flipped to pressed=true, the like registered via a different path
        return pressed == "true"


def heart_all(
    p,
    exe: str | None,
    items: list[tuple[str, str, str]],
    gmail,
) -> tuple[int, int, int, list[tuple[str, str, str]]]:
    """
    Click Like on each post and mark the email read immediately on success.

    items is a list of (msg_id, post_url, label) triples.
    Returns (hearted_count, failed_count, age_skipped_count, failed_items) where
    failed_items is the subset of items that failed and are eligible for retry.

    A fresh page is created per post to prevent browser state accumulation
    from causing JS rendering failures across a long session.
    """
    hearted = failed = age_skipped = 0
    failed_items: list[tuple[str, str, str]] = []
    ctx = _launch(p, exe, headless=True)
    try:
        for msg_id, post_url, label in items:
            log.info(f"  Liking: {label!r}")
            page = ctx.new_page()
            try:
                success = like_post(page, post_url)
            finally:
                page.close()
            if success:
                mark_as_read(gmail, msg_id)
                log.info(f"  Hearted + marked read: {label!r}")
                hearted += 1
            elif success is None:
                mark_as_read(gmail, msg_id)
                log.info(f"  Age-gated, marked read: {label!r}")
                age_skipped += 1
            else:
                log.warning(f"  Heart failed, leaving unread: {label!r}")
                failed += 1
                failed_items.append((msg_id, post_url, label))
            time.sleep(5)
    finally:
        ctx.close()
    return hearted, failed, age_skipped, failed_items


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    run_log = RunLogger()
    log.addHandler(run_log)
    hearted = failed = 0

    try:
        _main(run_log)
    except Exception:
        report_run("substack_heart", "crashed", hearted, failed, 0,
                   run_log.messages + [traceback.format_exc()])
        raise
    finally:
        log.removeHandler(run_log)


def _main(run_log):
    gmail = get_gmail_service()

    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    after_epoch = int(cutoff.timestamp())
    query = f"from:substack.com -from:no-reply@substack.com after:{after_epoch}"
    log.info(f"Searching Gmail: {query}")

    # (msg_id, subject, post_url)
    to_heart: list[tuple[str, str, str]] = []
    skipped = 0

    for msg_ref in iter_messages(gmail, query):
        msg = gmail.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()

        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        subject = headers.get("Subject", "(no subject)")
        sender = headers.get("From", "")

        if not is_subscriber_notification(sender):
            log.info(f"Skipping (not subscriber notification): {sender!r}")
            skipped += 1
            continue

        html = get_email_body_html(msg["payload"])
        if not html:
            log.warning(f"  No HTML body, skipping: {subject!r}")
            skipped += 1
            continue

        if not find_heart_link(html):
            log.info(f"  No Like link (notification-only email?), skipping: {subject!r}")
            skipped += 1
            continue

        post_url = find_post_url(html)
        if not post_url:
            log.warning(f"  No post URL found, skipping: {subject!r}")
            skipped += 1
            continue

        log.info(f"Found: {subject!r} → {post_url}")
        to_heart.append((msg_ref["id"], subject, post_url))

    if not to_heart:
        log.info(f"No Substack emails to heart (skipped {skipped}).")
        report_run("substack_heart", "success", 0, 0, skipped, run_log.messages)
        return

    log.info(f"{len(to_heart)} email(s) to heart, {skipped} skipped. Opening Playwright...")

    exe = get_chromium_executable()
    if not exe:
        log.warning("Full Chromium not found; falling back to Playwright default.")

    with sync_playwright() as p:
        if not check_substack_login(p, exe):
            interactive_login(p, exe)
            if not check_substack_login(p, exe):
                log.error("Still not logged in after interactive login. Aborting.")
                report_run("substack_heart", "crashed", 0, 0, skipped, run_log.messages)
                return

        items = [(msg_id, post_url, subject) for msg_id, subject, post_url in to_heart]
        hearted, failed, age_skipped, failed_items = heart_all(p, exe, items, gmail)
        skipped += age_skipped

        if failed_items:
            log.info(
                f"{len(failed_items)} failed — waiting {RETRY_DELAY_SECS}s before retry "
                f"(clears rate limits)..."
            )
            time.sleep(RETRY_DELAY_SECS)
            log.info("Retrying failed posts...")
            retry_hearted, failed, retry_age_skipped, _ = heart_all(p, exe, failed_items, gmail)
            hearted += retry_hearted
            skipped += retry_age_skipped
            if retry_hearted:
                log.info(f"  Retry recovered {retry_hearted} post(s).")

    log.info(f"Done. Hearted: {hearted}, Failed: {failed}, Skipped: {skipped}")
    report_run("substack_heart", "success", hearted, failed, skipped, run_log.messages)


if __name__ == "__main__":
    main()
