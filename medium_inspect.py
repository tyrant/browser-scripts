#!/usr/bin/env python3
"""Dump all links from a sample Medium subscriber notification email for inspection."""

import os
import base64
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "gmail_credentials.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "medium_gmail_token.json")


def get_service():
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


service = get_service()

result = service.users().messages().list(
    userId="me",
    q="from:subscriptions@medium.com newer_than:30d",
    maxResults=20,
).execute()
messages = result.get("messages", [])
print(f"Found {len(messages)} Medium subscription emails in last 30 days\n")

if not messages:
    print("No emails found. Check that you're authenticated as deathtomosttyrants@gmail.com.")
    raise SystemExit(1)

# Inspect the first 3 emails
for i, msg_ref in enumerate(messages[:3]):
    msg = service.users().messages().get(
        userId="me", id=msg_ref["id"], format="full"
    ).execute()
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    print(f"{'='*60}")
    print(f"Email {i+1}")
    print(f"Subject: {headers.get('Subject')}")
    print(f"From:    {headers.get('From')}")
    print()

    html = get_html(msg["payload"])
    if html:
        soup = BeautifulSoup(html, "html.parser")
        print("Links:")
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"]
            print(f"  [{text!r:30s}] -> {href[:100]}")
    else:
        print("No HTML body found.")
    print()
