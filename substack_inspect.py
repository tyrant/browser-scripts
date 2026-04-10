#!/usr/bin/env python3
"""Dump all links from a sample Substack subscriber notification email for inspection."""

import os
import base64
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = os.path.join(SCRIPT_DIR, "gmail_token.json")
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "gmail_credentials.json")


def get_service():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def get_html(payload):
    if payload.get("mimeType") == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        result = get_html(part)
        if result:
            return result
    return None


def is_subscriber_notification(sender):
    """Return True for *@substack.com senders except no-reply@substack.com."""
    sender_lower = sender.lower()
    return "@substack.com" in sender_lower and "no-reply@substack.com" not in sender_lower


service = get_service()

# Fetch recent emails from the substack.com domain
result = service.users().messages().list(
    userId="me",
    q="from:substack.com newer_than:30d",
    maxResults=50,
).execute()
messages = result.get("messages", [])
print(f"Found {len(messages)} substack.com emails in last 30 days\n")

# Find the first one that's a subscriber notification (not no-reply)
target_msg = None
for msg_ref in messages:
    msg = service.users().messages().get(
        userId="me", id=msg_ref["id"], format="full"
    ).execute()
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    sender = headers.get("From", "")
    if is_subscriber_notification(sender):
        target_msg = msg
        target_headers = headers
        break

if not target_msg:
    print("No subscriber notification emails found. All recent substack.com emails:")
    for msg_ref in messages[:10]:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        print(f"  From: {headers.get('From')}  |  Subject: {headers.get('Subject')}")
else:
    print(f"Subject: {target_headers.get('Subject')}")
    print(f"From:    {target_headers.get('From')}\n")

    html = get_html(target_msg["payload"])
    if html:
        soup = BeautifulSoup(html, "html.parser")
        print("=== All links in this email ===")
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"]
            print(f"  [{text!r}] -> {href[:120]}")
    else:
        print("No HTML body found.")
