#!/usr/bin/env python3
"""Re-auth the server's Substack or Medium session.

Run on a machine with a display. The server profile can't be refreshed by
rsyncing the macOS one — macOS encrypts cookies with a Keychain key Linux can't
read — so we read the decrypted session cookies locally and inject them into the
server's profile, where Linux re-encrypts them with its own key.

Usage: venv/bin/python reauth_server.py [substack|medium]
"""

import base64
import importlib
import json
import subprocess
import sys

from playwright.sync_api import sync_playwright

SERVER = "noob@168.144.167.177"
REMOTE = "/home/noob/scripts"
# Cloudflare/ALB cookies are IP- and user-agent-bound; the server reissues them.
DROP_COOKIES = {"cf_clearance", "__cf_bm", "AWSALBTG", "AWSALBTGCORS"}

TARGETS = {
    "substack": {
        "module": "substack_heart",
        "check": "check_substack_login",
        "domains": (".substack.com", "substack.com"),
    },
    "medium": {
        "module": "medium_clap",
        "check": "check_medium_login",
        "domains": (".medium.com", "medium.com"),
    },
}


def filter_session_cookies(cookies, target: str = "substack"):
    domains = TARGETS[target]["domains"]
    return [
        c for c in cookies
        if c.get("domain") in domains
        and c["name"] not in DROP_COOKIES
    ]


def build_remote_script(cookies_b64: str, remote: str = REMOTE, target: str = "substack") -> str:
    module, check = TARGETS[target]["module"], TARGETS[target]["check"]
    return f'''import base64, json, sys
sys.path.insert(0, "{remote}")
from {module} import get_chromium_executable, _launch, {check}
from playwright.sync_api import sync_playwright
cks = json.loads(base64.b64decode("{cookies_b64}"))
exe = get_chromium_executable()
with sync_playwright() as p:
    ctx = _launch(p, exe, headless=True)
    ctx.add_cookies(cks)
    ctx.close()
    print("injected", len(cks), "cookies")
    ok = {check}(p, exe)
    print("logged in:", ok)
    sys.exit(0 if ok else 1)
'''


def dump_local_cookies(target: str = "substack"):
    module = importlib.import_module(TARGETS[target]["module"])
    check_login = getattr(module, TARGETS[target]["check"])
    exe = module.get_chromium_executable()
    with sync_playwright() as p:
        if not check_login(p, exe):
            module.interactive_login(p, exe)
            if not check_login(p, exe):
                raise SystemExit(f"Local {target} login failed; aborting.")
        ctx = module._launch(p, exe, headless=True)
        cookies = filter_session_cookies(ctx.cookies(), target)
        ctx.close()
    return cookies


def inject_on_server(cookies, target: str = "substack") -> int:
    cookies_b64 = base64.b64encode(json.dumps(cookies).encode()).decode()
    script = build_remote_script(cookies_b64, target=target)
    script_b64 = base64.b64encode(script.encode()).decode()
    cmd = f"echo {script_b64} | base64 -d | {REMOTE}/venv/bin/python -"
    return subprocess.run(["ssh", SERVER, cmd]).returncode


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    target = argv[0] if argv else "substack"
    if target not in TARGETS:
        raise SystemExit(__doc__)
    cookies = dump_local_cookies(target)
    print(f"Dumped {len(cookies)} {target} session cookies; injecting on {SERVER}...")
    raise SystemExit(inject_on_server(cookies, target))


if __name__ == "__main__":
    main()
