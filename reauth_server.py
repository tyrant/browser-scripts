#!/usr/bin/env python3
"""Re-auth the server's Substack session for substack_heart.

Run on a machine with a display. The server profile can't be refreshed by
rsyncing the macOS one — macOS encrypts cookies with a Keychain key Linux can't
read — so we read the decrypted session cookies locally and inject them into the
server's profile, where Linux re-encrypts them with its own key.
"""

import base64
import json
import subprocess
import sys

from playwright.sync_api import sync_playwright

from substack_heart import (
    _launch,
    check_substack_login,
    get_chromium_executable,
    interactive_login,
)

SERVER = "noob@168.144.167.177"
REMOTE = "/home/noob/scripts"
# Cloudflare/ALB cookies are IP- and user-agent-bound; the server reissues them.
DROP_COOKIES = {"cf_clearance", "__cf_bm", "AWSALBTG", "AWSALBTGCORS"}


def filter_session_cookies(cookies):
    return [
        c for c in cookies
        if c.get("domain") in (".substack.com", "substack.com")
        and c["name"] not in DROP_COOKIES
    ]


def build_remote_script(cookies_b64: str, remote: str = REMOTE) -> str:
    return f'''import base64, json, sys
sys.path.insert(0, "{remote}")
from substack_heart import get_chromium_executable, _launch, check_substack_login
from playwright.sync_api import sync_playwright
cks = json.loads(base64.b64decode("{cookies_b64}"))
exe = get_chromium_executable()
with sync_playwright() as p:
    ctx = _launch(p, exe, headless=True)
    ctx.add_cookies(cks)
    ctx.close()
    print("injected", len(cks), "cookies")
    ok = check_substack_login(p, exe)
    print("logged in:", ok)
    sys.exit(0 if ok else 1)
'''


def dump_local_cookies():
    exe = get_chromium_executable()
    with sync_playwright() as p:
        if not check_substack_login(p, exe):
            interactive_login(p, exe)
            if not check_substack_login(p, exe):
                raise SystemExit("Local Substack login failed; aborting.")
        ctx = _launch(p, exe, headless=True)
        cookies = filter_session_cookies(ctx.cookies())
        ctx.close()
    return cookies


def inject_on_server(cookies) -> int:
    cookies_b64 = base64.b64encode(json.dumps(cookies).encode()).decode()
    script_b64 = base64.b64encode(build_remote_script(cookies_b64).encode()).decode()
    cmd = f"echo {script_b64} | base64 -d | {REMOTE}/venv/bin/python -"
    return subprocess.run(["ssh", SERVER, cmd]).returncode


def main():
    cookies = dump_local_cookies()
    print(f"Dumped {len(cookies)} session cookies; injecting on {SERVER}...")
    raise SystemExit(inject_on_server(cookies))


if __name__ == "__main__":
    main()
