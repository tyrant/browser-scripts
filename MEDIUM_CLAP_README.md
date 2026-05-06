# Medium Clap

Automatically claps on Medium subscriber notification emails received in the last 24 hours, then marks each email as read.

## What it does

1. Connects to Gmail via the Gmail API (as `deathtomosttyrants@gmail.com`) and searches for unread emails from `subscriptions@medium.com` received in the last 24 hours.
2. Extracts the post URL from the "Continue reading" link in each email, stripping tracking query parameters.
3. Skips any post URL recorded in `medium_clapped_urls.json` — posts clapped in a previous run are not clapped again.
4. Opens a headless Playwright browser with a saved Medium session, visits each post in a fresh browser tab, and clicks the Clap button a random number of times between 10 and 30.
5. Marks successfully clapped emails as read in Gmail and records the post URL in `medium_clapped_urls.json`. Failed ones are left unread so they'll be retried next run.

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
| `medium_clap.py` | The script |
| `gmail_credentials.json` | OAuth client credentials from Google Cloud Console (shared with substack_heart.py) |
| `medium_gmail_token.json` | Auto-generated OAuth token for `deathtomosttyrants@gmail.com` — do not commit |
| `medium_playwright_profile/` | Persistent Chromium profile storing the Medium session |
| `medium_clapped_urls.json` | Record of post URLs already clapped, to prevent re-clapping |
| `medium_clap.log` | Log output from the launchd job |

### First-time Gmail auth

On first run (or if `medium_gmail_token.json` is missing/expired), a browser window opens for Gmail OAuth. Make sure to select or log in as `deathtomosttyrants@gmail.com`. The token is saved for future runs.

The token needs `gmail.modify` scope to mark emails as read. If you accidentally create the token with `gmail.readonly` (e.g. by running `medium_inspect.py` first), delete `medium_gmail_token.json` and re-run to get the correct scope.

`deathtomosttyrants@gmail.com` must be added as a test user in the Google Cloud Console OAuth consent screen (APIs & Services → Audience) for the OAuth flow to succeed.

### First-time Medium login

On first run (or if the saved session has expired), the script opens a visible Chromium window and navigates to `medium.com/m/signin`. Log in manually, then press Enter in the terminal. The session is saved in `medium_playwright_profile/` and all subsequent runs are headless.

If you're ever prompted to log in again, the session has expired. Re-run the script manually and log in again.

## Scheduling

The script runs on the mikeyclarke.co.nz server at 00:05 UTC (12:05 NZST) via cron. See [README.md](README.md) for deployment and session management.

A launchd agent (`~/Library/LaunchAgents/local.medium_clap.plist`) exists for local scheduling but is currently disabled. To re-enable it:

```bash
launchctl load ~/Library/LaunchAgents/local.medium_clap.plist
```

## Behaviour details

- **Window**: searches emails from the last 24 hours. Re-running the same day will re-check all recent emails, but those already clapped will be skipped via `medium_clapped_urls.json`, and those already marked read will not appear in the search.
- **Clap count**: each post receives a random number of claps between 10 and 30, chosen independently per post.
- **Already clapped**: before opening the browser for a post, the script checks `medium_clapped_urls.json`. If the URL is present, the post is skipped and the email marked read without launching Playwright.
- **Inline mark-as-read**: emails are marked read and URLs recorded immediately after each successful clap, inside the browser session. A crash mid-run does not lose progress on posts already processed.
- **Fresh page per post**: a new browser tab is opened for each post and closed afterwards, preventing accumulated browser state from causing failures across a long session.

## Known limitations and gotchas

### Anti-automation detection
Medium detects headless browsers via `navigator.webdriver`. The script disables this with `--disable-blink-features=AutomationControlled` and sets a realistic user agent. If Medium presents a CAPTCHA loop during login, delete `medium_playwright_profile/` and re-run — the stale detected session is the cause.

### macOS cookie encryption
The script pins to the full Chromium binary (`Google Chrome for Testing.app`) rather than Playwright's headless-shell, for the same reason as `substack_heart.py`: the headless-shell uses a different cookie encryption path on macOS. The `--password-store=basic` flag bypasses Keychain-based password storage for consistency.

When deploying to Linux, macOS-encrypted cookies cannot be read directly. Use `export_session.py` / `import_session.py` to transfer the session — see [README.md](README.md).

### Session expiry
The Medium Playwright session is persistent but not permanent. If the session cookie expires, the script will open a visible browser for interactive login. Run it manually to complete the login flow.

### Clap button selector
The script finds the clap button via `button[data-testid*="clap" i]`, with fallbacks for `aria-label`-based selectors. If Medium updates their markup and the button is no longer found, the post is logged as failed and left unread.

### Medium's 50-clap cap
Medium allows a maximum of 50 claps per person per article across all time. The script does not track your cumulative clap count — if you have manually clapped a post before, the automated claps will add to that total and may hit the cap silently.
