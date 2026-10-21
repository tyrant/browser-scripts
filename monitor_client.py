"""Shared helper for reporting script run results to the monitor dashboard."""
import json
import logging
import os
import signal
import urllib.error
import urllib.request
from datetime import datetime, timezone

MONITOR_URL = "https://monitor.mikeyclarke.co.nz/api/run"

log = logging.getLogger(__name__)


class Terminated(Exception):
    pass


def raise_on_sigterm():
    """Turn SIGTERM into an exception so the run's crash handler still reports."""
    # systemd stops the whole scope with SIGTERM after an OOM kill inside it;
    # Python's default handler would exit silently, leaving no monitor record.
    def handler(signum, frame):
        raise Terminated(f"Killed by signal {signum} (systemd stopping the scope, often after an OOM kill)")
    signal.signal(signal.SIGTERM, handler)


class RunLogger(logging.Handler):
    """Captures WARNING+ log messages during a run for inclusion in the report."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(self.format(record))


def report_run(script, status, processed=0, failed=0, skipped=0, errors=None):
    api_key = os.environ.get("MONITOR_API_KEY", "")
    if not api_key:
        log.debug("MONITOR_API_KEY not set — skipping monitor report")
        return

    payload = json.dumps({
        "script": script,
        "status": status,
        "processed": processed,
        "failed": failed,
        "skipped": skipped,
        "errors": errors or [],
        "ran_at": datetime.now(timezone.utc).isoformat(),
    }).encode()

    req = urllib.request.Request(
        MONITOR_URL,
        data=payload,
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            log.debug(f"Monitor report accepted: {resp.status}")
    except Exception as e:
        log.warning(f"Monitor report failed (non-fatal): {e}")
