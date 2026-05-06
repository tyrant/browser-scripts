#!/usr/bin/env python3
"""Import a session JSON into a Playwright profile on Linux.

Clears the existing profile first to remove any macOS-encrypted cookies,
then creates a fresh profile seeded with the exported session.

Usage:
  python import_session.py <session.json> <profile_dir>

Example:
  python import_session.py medium_session.json medium_playwright_profile
  python import_session.py substack_session.json playwright_profile
"""
import glob
import json
import os
import shutil
import sys

from playwright.sync_api import sync_playwright

if len(sys.argv) != 3:
    print(__doc__)
    sys.exit(1)

state_file = os.path.abspath(sys.argv[1])
profile = os.path.abspath(sys.argv[2])

pattern = os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome")
matches = sorted(glob.glob(pattern))
exe = matches[-1] if matches else None
if not exe:
    print("Error: Playwright Chromium not found. Run: playwright install chromium")
    sys.exit(1)

with open(state_file) as f:
    state = json.load(f)

backup = profile + ".bak"
if os.path.exists(profile):
    os.rename(profile, backup)
    print(f"Backed up existing profile to {backup}")

try:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile,
            headless=True,
            executable_path=exe,
            args=["--password-store=basic"],
        )
        ctx.add_cookies(state["cookies"])
        ctx.close()
except Exception:
    if os.path.exists(backup):
        if os.path.exists(profile):
            shutil.rmtree(profile)
        os.rename(backup, profile)
        print("Import failed — restored original profile from backup.")
    raise

if os.path.exists(backup):
    shutil.rmtree(backup)

n = len(state["cookies"])
print(f"Profile created with {n} cookies at {profile}")
