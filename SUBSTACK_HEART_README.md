# Substack Heart

Automatically hearts (Likes) Substack subscriber notification emails received in the last 24 hours, then marks each email as read.

## What it does

1. Connects to Gmail via the Gmail API (as epicschnozz@gmail.com) and searches for unread emails from `*@substack.com` received in the last 24 hours.
2. Filters to subscriber notification emails only — skips `no-reply@substack.com`, `reaction@mg1.substack.com`, `forum@mg1.substack.com`, thread notifications, welcome emails, and anything without a Like link.
3. Extracts the post URL from each email (constructs `https://{pub}.substack.com/p/{slug}` directly to avoid redirect issues).
4. Opens a headless Playwright browser with a saved Substack session, visits each post in a fresh browser tab, and clicks the Like button.
5. Verifies the click registered by intercepting the `POST /api/v1/post/{id}/reaction` API call.
6. Marks successfully hearted emails as read in Gmail. Failed ones are left unread so they'll be retried next run.

## Setup

### Prerequisites

```bash
pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib \
            beautifulsoup4 playwright
playwright install chromium
```

### Files (all in `~/Work/scripts/`)

| File | Purpose |
|---|---|
| `substack_heart.py` | The script |
| `gmail_credentials.json` | OAuth client credentials from Google Cloud Console (gmail.modify scope) |
| `gmail_token.json` | Auto-generated OAuth token — do not commit |
| `playwright_profile/` | Persistent Chromium profile storing the Substack session |
| `substack_heart.log` | Log output from the launchd job |

### First-time Gmail auth

On first run (or if `gmail_token.json` is missing/expired), a browser window opens for Gmail OAuth. Grant access, then the token is saved for future runs.

The token needs `gmail.modify` scope (not `gmail.readonly`) to mark emails as read.

### Refreshing an expired Gmail token

Google periodically revokes refresh tokens. When this happens the script crashes with `invalid_grant: Token has been expired or revoked`. To fix:

```bash
# 1. Delete the expired token
rm ~/Work/scripts/gmail_token.json

# 2. Re-auth on the Mac (opens a browser window)
python3 ~/Work/scripts/substack_heart.py

# 3. Push the new token to the server
rsync -av ~/Work/scripts/gmail_token.json noob@168.144.167.177:/home/noob/scripts/
```

Use `rsync` directly — `deploy.sh` skips token files that already exist on the server.

### First-time Substack login

On first run (or if the saved session has expired), the script opens a visible Chromium window and navigates to `substack.com/sign-in`. Log in manually, then press Enter in the terminal. The session is saved in `playwright_profile/` and all subsequent runs are headless.

If you're ever prompted to log in again, the session cookie (`substack.sid`) has expired. Just re-run the script manually and log in again.

## Tests

Tests live in `tests/test_substack_heart.py` and cover all functions via mocks — no real Gmail API or Playwright browser needed.

### Running

```bash
cd ~/Work/scripts
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/pytest tests/ -v
```

### Coverage

| Area | What's tested |
|------|---------------|
| `is_subscriber_notification` | subscriber sender, no-reply, non-Substack |
| `get_email_body_html` | flat HTML payload, nested multipart, missing body |
| `find_heart_link` | submitLike link, app-link with Like text, no link |
| `find_post_url` | constructs native URL, no open.substack.com link |
| `get_gmail_service` | valid cached token, expired+refreshable, revoked token (RuntimeError), no token file (runs OAuth flow) |
| `iter_messages` | single page, multi-page pagination, empty result |
| `mark_as_read` | success, OSError retries with fresh service |
| `like_post` | sign-in redirect, age-verification redirect (skip/None), custom domain skip, Like button not found, already liked, successful like, 429 rate limit, no /reaction API captured |
| `check_substack_login` | cookie present, cookie absent |
| `heart_all` | all success, all failure, all age-gated, mixed outcomes |
| `main` | success path, exception reports crashed and reraises |
| `_main` | no emails, emails present with outcome reported, failed items trigger retry with aggregated counts |

## Scheduling

The script runs on the mikeyclarke.co.nz server at 00:00 UTC (noon NZST) via cron. See [README.md](README.md) for deployment and session management.

A launchd agent (`~/Library/LaunchAgents/local.substack_heart.plist`) exists for local scheduling but is currently disabled. To re-enable it:

```bash
launchctl load ~/Library/LaunchAgents/local.substack_heart.plist
```

## Behaviour details

- **Window**: searches emails from the last 24 hours, not just "unread". This means re-running the script the same day will re-check all recent emails but skip ones already marked read.
- **Already liked**: if the Like button's `aria-pressed` is already `"true"`, the post is skipped as already liked and the email is still marked read.
- **Rate limiting**: Substack rate-limits the `/reaction` API. When a 429 is received, the script waits 30 seconds before the next post. The 5-second base delay between posts reduces how often this happens.
- **Duplicate emails**: some posts generate two notification emails. The script processes both; the second attempt finds the button already pressed and counts as success.

## Known limitations and gotchas

### Custom domain publications
Some Substack publications use a custom domain (e.g. `geezerwise.substack.com` → `www.geezerwise.com`). The `substack.sid` session cookie is scoped to `*.substack.com` and doesn't transfer to custom domains. These posts are detected and skipped with a log message rather than silently failing.

### Rate limiting on heavy days
Substack limits how many posts you can like in quick succession. On days with many new emails, you may see several 429 responses. The script handles these gracefully (30s wait, then continues). The affected posts are left unread and will be picked up again the next day if the notification emails are still within the 24-hour window — which they won't be. Consider this acceptable attrition.

### macOS cookie encryption
The script pins to the full Chromium binary (`Google Chrome for Testing.app`) rather than Playwright's headless-shell, because the headless-shell uses a different cookie encryption path on macOS and can't read session cookies saved by the full browser. The `--password-store=basic` flag bypasses Keychain-based password storage for consistency.

When deploying to Linux, macOS-encrypted cookies cannot be read directly. Use `export_session.py` / `import_session.py` to transfer the session — see [README.md](README.md).

### Session expiry
The Playwright session is persistent but not immortal. If the `substack.sid` cookie expires (typically after several weeks of inactivity), the script will log "Still not logged in after interactive login" or similar. Run it manually to trigger the interactive login flow.

### Posts with no Like button
Notification emails for threads, welcome messages, and some digest formats don't include a Like link. These are filtered at the Gmail stage and skipped without visiting the browser. A small number of posts that pass the email filter still show no Like button in the browser (e.g. deleted posts, certain paywalled formats) — these are logged as failures and left unread.
