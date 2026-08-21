#!/usr/bin/env python3
"""
gmail_substack_archive.py — Hourly script to file away Substack emails.

Connects to the epicschnozz@gmail.com Gmail account, finds inbox emails received
in the last 24 hours whose sender address ends in '@substack.com', applies the
'Substack' label to each, and archives it (removes it from the inbox). Both are
done in a single idempotent modify call, so re-running is safe.

The Gmail search is narrowed server-side to `from:substack.com` for efficiency,
then each sender is checked strictly for an '@substack.com' ending (so lookalike
subdomains such as '@mail.substack.com' are left alone). Restricting to
`in:inbox` keeps re-runs cheap: already-archived mail drops out of the query.

Auth: shares gmail_credentials.json (the OAuth client) with the other scripts but
uses its own token, since this is a different Google account. First run (or
`--reauth`) opens a browser to log in to epicschnozz@gmail.com and consent.
Use `--dry-run` to log what would be filed without changing anything.
"""

import os
import sys
import logging
import traceback
from email.utils import parseaddr
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from monitor_client import RunLogger, report_run

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "gmail_credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "epicschnozz_gmail_token.json")
ACCOUNT = "epicschnozz@gmail.com"
LABEL_NAME = "Substack"
SENDER_SUFFIX = "@substack.com"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


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
                        f"Gmail token for {ACCOUNT} revoked. Re-auth with "
                        "'python3 gmail_substack_archive.py --reauth', then rsync the new "
                        "token to the server. See GMAIL_SUBSTACK_ARCHIVE_README.md."
                    ) from e
                raise
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def get_or_create_label(service, name: str) -> str:
    """Return the id of the label named `name`, creating it if it doesn't exist."""
    existing = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in existing:
        if label["name"] == name:
            return label["id"]
    created = service.users().labels().create(
        userId="me",
        body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
    ).execute()
    log.info(f"Created label {name!r} ({created['id']}).")
    return created["id"]


def sender_address(from_header: str) -> str:
    """The bare email address from a From header, lowercased (e.g. 'a@substack.com')."""
    return parseaddr(from_header)[1].lower()


def is_substack_sender(from_header: str) -> bool:
    return sender_address(from_header).endswith(SENDER_SUFFIX)


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


def label_and_archive(service, msg_id: str, label_id: str) -> None:
    """Apply the Substack label and remove INBOX (archive) in one idempotent call.

    Retries once with a fresh service if the connection went stale, matching the
    resilience pattern used elsewhere in these scripts.
    """
    body = {"addLabelIds": [label_id], "removeLabelIds": ["INBOX"]}
    try:
        service.users().messages().modify(userId="me", id=msg_id, body=body).execute()
    except (TimeoutError, OSError) as e:
        log.warning(f"Gmail connection dropped, retrying with fresh service: {e}")
        get_gmail_service().users().messages().modify(userId="me", id=msg_id, body=body).execute()


# ── Main ──────────────────────────────────────────────────────────────────────

def main(dry_run: bool = False):
    run_log = RunLogger()
    log.addHandler(run_log)
    processed = failed = skipped = 0

    try:
        processed, failed, skipped = _main(dry_run)
        report_run("gmail_substack_archive", "success", processed, failed, skipped, run_log.messages)
    except Exception:
        report_run("gmail_substack_archive", "crashed", processed, failed, skipped,
                   run_log.messages + [traceback.format_exc()])
        raise
    finally:
        log.removeHandler(run_log)


def _main(dry_run: bool) -> tuple[int, int, int]:
    gmail = get_gmail_service()
    label_id = None if dry_run else get_or_create_label(gmail, LABEL_NAME)

    cutoff = datetime.now(timezone.utc) - timedelta(days=1)
    after_epoch = int(cutoff.timestamp())
    query = f"in:inbox from:substack.com after:{after_epoch}"
    log.info(f"[{ACCOUNT}] Searching Gmail: {query}" + (" (dry-run)" if dry_run else ""))

    processed = failed = skipped = 0
    for msg_ref in iter_messages(gmail, query):
        msg = gmail.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata", metadataHeaders=["From", "Subject"]
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        sender = headers.get("From", "")
        subject = headers.get("Subject", "(no subject)")

        # `from:substack.com` over-matches subdomains; enforce the exact suffix.
        if not is_substack_sender(sender):
            log.info(f"Skipping (not @substack.com): {sender!r}")
            skipped += 1
            continue

        if dry_run:
            log.info(f"[dry-run] would label+archive: {subject!r} — {sender_address(sender)}")
            processed += 1
            continue

        try:
            label_and_archive(gmail, msg_ref["id"], label_id)
            log.info(f"Labelled + archived: {subject!r} — {sender_address(sender)}")
            processed += 1
        except Exception as e:
            log.warning(f"Failed to file {subject!r}: {e}")
            failed += 1

    verb = "would file" if dry_run else "filed"
    log.info(f"Done. {verb.capitalize()}: {processed}, Failed: {failed}, Skipped: {skipped}")
    return processed, failed, skipped


def reauth():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    get_gmail_service()
    log.info(f"New token for {ACCOUNT} written to {TOKEN_FILE}. Copy it to the server now.")


if __name__ == "__main__":
    if "--reauth" in sys.argv:
        reauth()
    else:
        main(dry_run="--dry-run" in sys.argv)
