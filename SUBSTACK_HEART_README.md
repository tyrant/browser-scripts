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

### First-time Substack login

On first run (or if the saved session has expired), the script opens a visible Chromium window and navigates to `substack.com/sign-in`. Log in manually, then press Enter in the terminal. The session is saved in `playwright_profile/` and all subsequent runs are headless.

If you're ever prompted to log in again, the session cookie (`substack.sid`) has expired. Just re-run the script manually and log in again.

## Daily job (launchd)

The script runs automatically every day at noon via a launchd agent:

```
~/Library/LaunchAgents/local.substack_heart.plist
```

### Useful commands

```bash
# Check the job is registered and see last exit code
launchctl list | grep substack_heart

# Run it right now (without waiting for noon)
launchctl start local.substack_heart

# Watch the log live
tail -f ~/Work/scripts/substack_heart.log

# Remove the job (stops scheduling)
launchctl unload ~/Library/LaunchAgents/local.substack_heart.plist

# Re-register after editing the plist
launchctl unload ~/Library/LaunchAgents/local.substack_heart.plist
launchctl load   ~/Library/LaunchAgents/local.substack_heart.plist
```

In `launchctl list` output, a `-` in the PID column means it's not currently running (correct when idle). The second column is the last exit code — `0` means success.

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
The script pins to the full Chromium binary (`Google Chrome for Testing.app`) rather than Playwright's headless-shell, because the headless-shell uses a different cookie encryption path on macOS and can't read session cookies saved by the full browser. The `--password-store=basic` flag bypasses Keychain-based encryption entirely for consistency.

### Session expiry
The Playwright session is persistent but not immortal. If the `substack.sid` cookie expires (typically after several weeks of inactivity), the script will log "Still not logged in after interactive login" or similar. Run it manually to trigger the interactive login flow.

### Posts with no Like button
Notification emails for threads, welcome messages, and some digest formats don't include a Like link. These are filtered at the Gmail stage and skipped without visiting the browser. A small number of posts that pass the email filter still show no Like button in the browser (e.g. deleted posts, certain paywalled formats) — these are logged as failures and left unread.
