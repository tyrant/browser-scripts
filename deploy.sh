#!/usr/bin/env bash
set -euo pipefail

SERVER="noob@168.144.167.177"
REMOTE="/home/noob/scripts"
LOCAL="$(cd "$(dirname "$0")" && pwd)"

echo "==> Syncing code to $SERVER:$REMOTE"
rsync -av \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.log' \
  --exclude='venv' \
  --exclude='tests' \
  --exclude='playwright_profile' \
  --exclude='medium_playwright_profile' \
  --exclude='gmail_token.json' \
  --exclude='medium_gmail_token.json' \
  --exclude='epicschnozz_gmail_token.json' \
  --exclude='medium_clapped_urls.json' \
  "$LOCAL/" "$SERVER:$REMOTE/"

echo "==> Syncing session data (first deploy only — skips existing files)"
rsync -av --ignore-existing \
  "$LOCAL/gmail_token.json" \
  "$LOCAL/medium_gmail_token.json" \
  "$LOCAL/epicschnozz_gmail_token.json" \
  "$SERVER:$REMOTE/" 2>/dev/null || true

if [ -f "$LOCAL/medium_clapped_urls.json" ]; then
  rsync -av --ignore-existing \
    "$LOCAL/medium_clapped_urls.json" \
    "$SERVER:$REMOTE/" 2>/dev/null || true
fi

# playwright_profile is NOT synced: macOS encrypts its cookies with a Keychain
# key Linux can't read, so a copied session decrypts to nothing on the server.
# Re-auth with: venv/bin/python reauth_server.py [substack|medium]

rsync -av --ignore-existing \
  "$LOCAL/medium_playwright_profile/" \
  "$SERVER:$REMOTE/medium_playwright_profile/" 2>/dev/null || true

echo "==> Installing dependencies"
ssh "$SERVER" bash <<'ENDSSH'
  set -euo pipefail
  cd /home/noob/scripts

  if [ ! -d venv ]; then
    echo "Creating venv..."
    python3 -m venv venv
  fi

  venv/bin/pip install -q -r requirements.txt
  venv/bin/playwright install chromium
ENDSSH

echo "==> Updating crontab"
ssh "$SERVER" bash <<'ENDSSH'
  set -euo pipefail
  MONITOR_KEY=$(grep '^MONITOR_API_KEY=' /home/noob/monitor/.env | head -1)
  # Browser jobs run inside a memory/CPU-capped systemd scope + a flock (no
  # overlapping runs), so a runaway Chromium is OOM-killed in its own cgroup
  # rather than taking the box down. medium_clap runs on the Mac via launchd:
  # Cloudflare challenges Medium requests from this datacenter IP.
  # The reaper kills any leaked automation Chromium older than 10 min.
  (crontab -l 2>/dev/null | grep -v 'substack_heart\|medium_clap\|gmail_substack_archive\|MONITOR_API_KEY\|reap-stale-chrome'; cat <<CRON
$MONITOR_KEY
*/5 * * * * /home/noob/bin/reap-stale-chrome.sh 10 >> /home/noob/log/chrome-reaper.log 2>&1
0 0 * * * XDG_RUNTIME_DIR=/run/user/1000 /usr/bin/flock -n /tmp/substack_heart.lock /usr/bin/systemd-run --user --scope -p MemoryMax=1G -p MemorySwapMax=512M -p CPUQuota=80\% /home/noob/scripts/venv/bin/python /home/noob/scripts/substack_heart.py >> /home/noob/scripts/substack_heart.log 2>&1
15 * * * * /usr/bin/flock -n /tmp/gmail_substack_archive.lock /home/noob/scripts/venv/bin/python /home/noob/scripts/gmail_substack_archive.py >> /home/noob/scripts/gmail_substack_archive.log 2>&1
CRON
  ) | crontab -
  echo "Crontab updated:"
  crontab -l
ENDSSH

echo ""
echo "==> Done. Heart runs daily at 00:00 UTC (memory-capped); Gmail archive runs hourly at :15. Clap runs on the Mac (launchd)."
echo ""
echo "    If the Substack session has expired (heart run reports crashed):"
echo "      venv/bin/python reauth_server.py"
echo ""
echo "    To disable the local Heart launchd agent:"
echo "      launchctl unload -w ~/Library/LaunchAgents/local.substack_heart.plist"
