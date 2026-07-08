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
# 1. Re-auth on the Mac (deletes the old token, opens a browser window,
#    writes a fresh gmail_token.json, then exits without hearting anything)
python3 ~/Work/scripts/substack_heart.py --reauth

# 2. Push the new token to the server
rsync -av ~/Work/scripts/gmail_token.json noob@168.144.167.177:/home/noob/scripts/
```

`--reauth` deletes `gmail_token.json` itself and runs only the Gmail OAuth flow — it skips the full email-hearting run, so re-authenticating no longer wastes time driving the browser.

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
| `like_post` | sign-in redirect, age-verification redirect (skip/None), custom domain → API heart, Like button not found, already liked, successful like, 429 retried then recovered, 429 every attempt, no /reaction API captured |
| `heart_via_api` | success, unparseable URL, post lookup non-200, missing id, reaction non-2xx, posts reaction body to apex host |
| `check_substack_login` | cookie present, cookie absent |
| `heart_all` | all success, all failure, all age-gated, mixed outcomes |
| `main` | success path, exception reports crashed and reraises |
| `_main` | no emails, emails present with outcome reported, failed items trigger retry with aggregated counts |

## Scheduling

The script runs on the mikeyclarke.co.nz server at 00:00 UTC (noon NZST) via cron. See [README.md](README.md) for deployment and session management. The server cron is the single source of truth.

A launchd agent (`~/Library/LaunchAgents/local.substack_heart.plist`) exists for local scheduling. It must stay **unloaded** — it fires at local noon, the same wall-clock moment as the server cron, so loading it makes two browsers drive the same Substack session at once. That doubles the reaction-API request rate (worsening 429s) and posts a duplicate run to the monitor dashboard. Keep it off:

```bash
launchctl unload ~/Library/LaunchAgents/local.substack_heart.plist
```

## Behaviour details

- **Window**: searches emails from the last 24 hours, not just "unread". This means re-running the script the same day will re-check all recent emails but skip ones already marked read.
- **Already liked**: if the Like button's `aria-pressed` is already `"true"`, the post is skipped as already liked and the email is still marked read.
- **Rate limiting**: Substack rate-limits the `/reaction` API. There is a 12-second base delay between posts (`POST_DELAY_SECS`). On a 429 the script retries the *same* post in place with exponential backoff (`REACTION_BACKOFFS` = 30 → 60 → 120s) rather than failing it, so a rate-limited like is recovered on the spot instead of left for the bulk retry pass.
- **Duplicate emails**: some posts generate two notification emails. The script processes both; the second attempt finds the button already pressed and counts as success.

## Known limitations and gotchas

### Custom domain publications
Some Substack publications use a custom domain (e.g. `geezerwise.substack.com` → `www.geezerwise.com`). The post page redirects off `*.substack.com`, so the `substack.sid` cookie isn't sent and the Like button can't be clicked. These are now hearted via the reaction API instead (`heart_via_api`): the post id is resolved from the public `https://{pub}.substack.com/api/v1/posts/{slug}` endpoint, then the like is POSTed to the apex `https://substack.com/api/v1/post/{id}/reaction` — the apex host is in `*.substack.com` scope, doesn't redirect, and accepts the like by post id. Like (`POST`) and unlike (`DELETE`) are separate verbs, so re-running on a duplicate email never unlikes.

### Rate limiting on heavy days
Substack limits how many posts you can like in quick succession. On days with many new emails you'll see 429 responses. The script now retries each 429'd post in place with exponential backoff before moving on, so most are recovered within the same run. Anything still failing after the backoffs is left unread for the bulk retry pass. Running the script from two schedulers at once (see Scheduling) dramatically worsens 429s — keep it to the single server cron.

### macOS cookie encryption
The script pins to the full Chromium binary (`Google Chrome for Testing.app`) rather than Playwright's headless-shell, because the headless-shell uses a different cookie encryption path on macOS and can't read session cookies saved by the full browser. The `--password-store=basic` flag bypasses Keychain-based password storage for consistency.

When deploying to Linux, macOS-encrypted cookies cannot be read directly. Use `export_session.py` / `import_session.py` to transfer the session — see [README.md](README.md).

### Session expiry
The Playwright session is persistent but not immortal. If the `substack.sid` cookie expires (typically after several weeks of inactivity), the script will log "Still not logged in after interactive login" or similar. Run it manually to trigger the interactive login flow.

### Posts with no Like button
Notification emails for threads, welcome messages, and some digest formats don't include a Like link. These are filtered at the Gmail stage and skipped without visiting the browser. A small number of posts that pass the email filter still show no Like button in the browser (e.g. deleted posts, certain paywalled formats) — these are logged as failures and left unread.
