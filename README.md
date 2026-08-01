# Scripts

Daily automation scripts that run on the mikeyclarke.co.nz server and report results to the [Script Monitor](../monitor/README.md).

## Scripts

| Script | What it does |
|---|---|
| `substack_heart.py` | Hearts Substack subscriber notification emails received in the last 24 hours |
| `medium_clap.py` | Claps Medium subscriber notification emails received in the last 24 hours |
| `gmail_substack_archive.py` | Labels ('Substack') and archives `@substack.com` emails in the epicschnozz@gmail.com inbox from the last 24 hours |

See [SUBSTACK_HEART_README.md](SUBSTACK_HEART_README.md), [MEDIUM_CLAP_README.md](MEDIUM_CLAP_README.md), and [GMAIL_SUBSTACK_ARCHIVE_README.md](GMAIL_SUBSTACK_ARCHIVE_README.md) for full detail on each script's behaviour, edge cases, and Gmail setup.

## Shared files

| File | Purpose |
|---|---|
| `gmail_credentials.json` | OAuth client credentials from Google Cloud Console — shared by all Gmail scripts |
| `gmail_token.json` | OAuth token for `substack_heart.py` — do not commit |
| `medium_gmail_token.json` | OAuth token for `medium_clap.py` — do not commit |
| `epicschnozz_gmail_token.json` | OAuth token for `gmail_substack_archive.py` (epicschnozz@gmail.com) — do not commit |
| `playwright_profile/` | Substack browser session — do not commit |
| `medium_playwright_profile/` | Medium browser session — do not commit |
| `medium_clapped_urls.json` | Already-clapped post URLs — do not commit |
| `monitor_client.py` | Shared helper that POSTs run results to the monitor dashboard |
| `requirements.txt` | Python dependencies |

## Server deployment

Scripts run on the server at `119.9.131.4` via cron. `deploy.sh` syncs code on subsequent deploys; first-time setup is handled by the same script if the venv doesn't exist yet.

```bash
bash deploy.sh
```

This rsyncs Python files to `/home/noob/scripts/`, installs dependencies, and updates the crontab. Session data files (playwright profiles, token files, clapped URLs) are only copied if they don't already exist on the server.

### Cron schedule

The scripts run daily, staggered (times UTC):

```
MONITOR_API_KEY=...
0 0 * * * .../substack_heart.py >> .../substack_heart.log 2>&1          # midnight — browser, memory-capped
0 1 * * * .../medium_clap.py >> .../medium_clap.log 2>&1                # 01:00 — browser, memory-capped
0 2 * * * .../gmail_substack_archive.py >> .../gmail_substack_archive.log 2>&1   # 02:00 — Gmail API only, lightweight
```

`gmail_substack_archive.py` is a pure Gmail-API job (no browser), so it runs without the systemd memory scope the two Playwright scripts use.

### Server logs

```bash
ssh noob@119.9.131.4
tail -f /home/noob/scripts/substack_heart.log
tail -f /home/noob/scripts/medium_clap.log
tail -f /home/noob/scripts/gmail_substack_archive.log
```

## Session management

Playwright browser sessions are stored in the profile directories. Because macOS encrypts cookies with the Keychain, profiles copied directly from Mac to Linux produce unreadable sessions. Use the export/import helpers instead.

### Refreshing a session on the server

Run on Mac:

```bash
python3 export_session.py medium_playwright_profile medium_session.json
python3 export_session.py playwright_profile substack_session.json
```

Transfer to server:

```bash
rsync -av medium_session.json substack_session.json noob@119.9.131.4:/home/noob/scripts/
```

Import on server:

```bash
ssh noob@119.9.131.4
cd /home/noob/scripts
venv/bin/python import_session.py medium_session.json medium_playwright_profile
venv/bin/python import_session.py substack_session.json playwright_profile
```

`import_session.py` backs up the existing profile before replacing it and restores the backup if the import fails.

## Monitor integration

Both scripts report their results to the monitor dashboard via `monitor_client.py`. Results are posted to `https://monitor.mikeyclarke.co.nz/api/run` using the `MONITOR_API_KEY` environment variable. If the key is not set, reporting is silently skipped.

The monitor can also trigger a run on demand via the **Run now** button on the dashboard.

## Local dev

The launchd agents (`local.substack_heart` and `local.medium_clap`) are currently disabled — scripts run on the server. To run a script locally for testing:

```bash
cd ~/Work/scripts
python3 substack_heart.py
python3 medium_clap.py
```

To re-enable launchd scheduling (e.g. when working locally):

```bash
launchctl load ~/Library/LaunchAgents/local.substack_heart.plist
launchctl load ~/Library/LaunchAgents/local.medium_clap.plist
```
