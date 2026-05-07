#!/usr/bin/env bash
set -euo pipefail

SERVER="noob@119.9.131.4"
REMOTE="/home/noob/scripts"
LOCAL="$(cd "$(dirname "$0")" && pwd)"

echo "==> Syncing code to $SERVER:$REMOTE"
rsync -av \
  --exclude='.git' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.log' \
  --exclude='playwright_profile' \
  --exclude='medium_playwright_profile' \
  --exclude='gmail_token.json' \
  --exclude='medium_gmail_token.json' \
  --exclude='medium_clapped_urls.json' \
  "$LOCAL/" "$SERVER:$REMOTE/"

echo "==> Syncing session data (first deploy only — skips existing files)"
rsync -av --ignore-existing \
  "$LOCAL/gmail_token.json" \
  "$LOCAL/medium_gmail_token.json" \
  "$SERVER:$REMOTE/" 2>/dev/null || true

if [ -f "$LOCAL/medium_clapped_urls.json" ]; then
  rsync -av --ignore-existing \
    "$LOCAL/medium_clapped_urls.json" \
    "$SERVER:$REMOTE/" 2>/dev/null || true
fi

rsync -av --ignore-existing \
  "$LOCAL/playwright_profile/" \
  "$SERVER:$REMOTE/playwright_profile/" 2>/dev/null || true

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
ssh "$SERVER" bash <<ENDSSH
  set -euo pipefail
  (crontab -l 2>/dev/null | grep -v 'substack_heart\|medium_clap\|MONITOR_API_KEY'; cat <<'CRON'
MONITOR_API_KEY=159d88522491bc377504514f98f4f7a7d6f2eee747a4b4d15c0250938716d560
0 0 * * * /home/noob/scripts/venv/bin/python /home/noob/scripts/substack_heart.py >> /home/noob/scripts/substack_heart.log 2>&1
5 0 * * * /home/noob/scripts/venv/bin/python /home/noob/scripts/medium_clap.py >> /home/noob/scripts/medium_clap.log 2>&1
CRON
  ) | crontab -
  echo "Crontab updated:"
  crontab -l
ENDSSH

echo ""
echo "==> Done. Scripts will run daily at 00:00 and 00:05 UTC (noon and 12:05 NZST)."
echo ""
echo "    To disable the local launchd agents now:"
echo "      launchctl unload ~/Library/LaunchAgents/local.substack_heart.plist"
echo "      launchctl unload ~/Library/LaunchAgents/local.medium_clap.plist"
