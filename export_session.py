#!/usr/bin/env python3
"""Export a Playwright browser session to plain JSON for transfer to another machine.

Usage:
  python export_session.py <profile_dir> <output.json>

Example:
  python export_session.py medium_playwright_profile medium_session.json
  python export_session.py playwright_profile substack_session.json
"""
import glob
import json
import os
import sys

from playwright.sync_api import sync_playwright

if len(sys.argv) != 3:
    print(__doc__)
    sys.exit(1)

profile = os.path.abspath(sys.argv[1])
output = sys.argv[2]

pattern = os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium-*/"
    "chrome-mac-*/Google Chrome for Testing.app/"
    "Contents/MacOS/Google Chrome for Testing"
)
matches = sorted(glob.glob(pattern))
exe = matches[-1] if matches else None

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        profile, headless=True, executable_path=exe, args=["--password-store=basic"]
    )
    state = ctx.storage_state()
    ctx.close()

with open(output, "w") as f:
    json.dump(state, f, indent=2)

n_cookies = len(state.get("cookies", []))
print(f"Exported {n_cookies} cookies to {output}")
