# Gmail Substack Archive

`gmail_substack_archive.py` — a daily Gmail-API job (no browser) that tidies the
**epicschnozz@gmail.com** inbox.

## What it does

Once a day it queries Gmail for inbox messages received in the last 24 hours,
keeps those whose sender address ends in `@substack.com`, applies the **Substack**
label to each, and archives it (removes it from the inbox). Label + archive happen
in a single `messages.modify` call, which is idempotent — re-running never doubles
up, and restricting the search to `in:inbox` means already-archived mail simply
drops out of the query.

The search is narrowed server-side to `from:substack.com` for efficiency, then each
sender is checked strictly for an `@substack.com` ending, so lookalikes such as
`@mail.substack.com` are deliberately left alone. `no-reply@substack.com` **is**
included (unlike `substack_heart.py`, which skips it).

## Setup

### Prerequisites

- `gmail_credentials.json` — the shared OAuth client (already present for the other scripts).
- Dependencies from `requirements.txt` (the `google-*` packages).

### Files (all in `~/Work/scripts/`)

| File | Purpose |
|---|---|
| `gmail_substack_archive.py` | The script |
| `gmail_credentials.json` | Shared OAuth client — do not commit |
| `epicschnozz_gmail_token.json` | OAuth token for epicschnozz@gmail.com — do not commit |

### First-time Gmail auth

The token file doesn't exist yet, so the first run opens a browser to log in and
consent. **Log in as epicschnozz@gmail.com** (not any other Google account):

```bash
cd ~/Work/scripts
venv/bin/python gmail_substack_archive.py --reauth
```

This writes `epicschnozz_gmail_token.json`. `deploy.sh` copies it to the server on
the next deploy (only if it doesn't already exist there).

### Refreshing an expired/revoked token

```bash
# On the Mac: delete the old token, re-auth, write a fresh one (no archiving)
venv/bin/python gmail_substack_archive.py --reauth
# Then push it to the server
rsync -av epicschnozz_gmail_token.json noob@168.144.167.177:/home/noob/scripts/
```

## Dry run

Preview what would be filed without changing anything:

```bash
venv/bin/python gmail_substack_archive.py --dry-run
```

## Tests

```bash
venv/bin/pytest tests/test_gmail_substack_archive.py
```

Covers sender matching, label lookup/creation, pagination, the label+archive modify
call (and its stale-connection retry), the end-to-end `_main` (files Substack mail,
skips others, dry-run mutates nothing), auth (cached/revoked/reauth), and monitor
reporting. The Gmail service is mocked — no live API calls.

## Scheduling

Runs at **02:00 UTC** daily via cron (see `deploy.sh`), staggered after the two
Playwright scripts. Being a pure API job it runs without their systemd memory scope.
Results report to the monitor dashboard as `gmail_substack_archive`.

## Behaviour details

- **Idempotent** — modify calls are no-ops on already-labelled/archived mail.
- **Window** — a rolling last-24-hours (`after:<epoch>`), matching the sibling scripts.
- **Scope** — `gmail.modify`, which permits both label creation and archiving.
