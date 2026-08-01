"""Tests for gmail_substack_archive.py."""
from unittest.mock import MagicMock, mock_open, patch

import pytest

from gmail_substack_archive import (
    LABEL_NAME,
    TOKEN_FILE,
    _main,
    get_gmail_service,
    get_or_create_label,
    is_substack_sender,
    iter_messages,
    label_and_archive,
    main,
    reauth,
    sender_address,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _msg(msg_id, sender, subject="(subj)"):
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ]
        },
    }


def _service(list_pages, messages_by_id, labels=None):
    """A MagicMock Gmail service wired for list/get/modify/labels calls."""
    svc = MagicMock()
    messages = svc.users.return_value.messages.return_value
    messages.list.return_value.execute.side_effect = list_pages
    messages.get.return_value.execute.side_effect = [messages_by_id[i] for i in messages_by_id]
    messages.modify.return_value.execute.return_value = {}
    svc.users.return_value.labels.return_value.list.return_value.execute.return_value = {
        "labels": labels if labels is not None else [{"id": "LBL", "name": LABEL_NAME}]
    }
    svc.users.return_value.labels.return_value.create.return_value.execute.return_value = {"id": "NEW"}
    return svc, messages


# ── sender parsing ─────────────────────────────────────────────────────────────

class TestSenderMatching:
    def test_plain_address(self):
        assert sender_address("writer@substack.com") == "writer@substack.com"

    def test_named_address_is_lowercased(self):
        assert sender_address("Some Writer <Writer@Substack.com>") == "writer@substack.com"

    @pytest.mark.parametrize("sender", [
        "writer@substack.com",
        "Some Writer <writer@substack.com>",
        "no-reply@substack.com",
    ])
    def test_substack_senders_match(self, sender):
        assert is_substack_sender(sender) is True

    @pytest.mark.parametrize("sender", [
        "writer@mail.substack.com",
        "writer@notsubstack.com",
        "writer@substack.com.evil.com",
        "writer@gmail.com",
        "",
    ])
    def test_non_substack_senders_do_not_match(self, sender):
        assert is_substack_sender(sender) is False


# ── label lookup / creation ─────────────────────────────────────────────────────

class TestGetOrCreateLabel:
    def test_returns_existing_label_id(self):
        svc = MagicMock()
        svc.users.return_value.labels.return_value.list.return_value.execute.return_value = {
            "labels": [{"id": "L1", "name": "Other"}, {"id": "L2", "name": LABEL_NAME}]
        }
        assert get_or_create_label(svc, LABEL_NAME) == "L2"

    def test_creates_label_when_absent(self):
        svc = MagicMock()
        labels = svc.users.return_value.labels.return_value
        labels.list.return_value.execute.return_value = {"labels": [{"id": "L1", "name": "Other"}]}
        labels.create.return_value.execute.return_value = {"id": "NEW"}
        assert get_or_create_label(svc, LABEL_NAME) == "NEW"

    def test_create_uses_the_configured_name(self):
        svc = MagicMock()
        labels = svc.users.return_value.labels.return_value
        labels.list.return_value.execute.return_value = {"labels": []}
        labels.create.return_value.execute.return_value = {"id": "NEW"}
        get_or_create_label(svc, LABEL_NAME)
        assert labels.create.call_args.kwargs["body"]["name"] == LABEL_NAME


# ── pagination ──────────────────────────────────────────────────────────────────

class TestIterMessages:
    def test_single_page(self):
        svc = MagicMock()
        svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "1"}, {"id": "2"}]
        }
        assert [m["id"] for m in iter_messages(svc, "q")] == ["1", "2"]

    def test_follows_next_page_token(self):
        svc = MagicMock()
        svc.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "1"}], "nextPageToken": "t"},
            {"messages": [{"id": "2"}]},
        ]
        assert [m["id"] for m in iter_messages(svc, "q")] == ["1", "2"]


# ── label + archive ────────────────────────────────────────────────────────────

class TestLabelAndArchive:
    def test_adds_label_and_removes_inbox(self):
        svc = MagicMock()
        label_and_archive(svc, "42", "LBL")
        body = svc.users.return_value.messages.return_value.modify.call_args.kwargs["body"]
        assert body == {"addLabelIds": ["LBL"], "removeLabelIds": ["INBOX"]}

    @patch("gmail_substack_archive.get_gmail_service")
    def test_retries_with_fresh_service_on_dropped_connection(self, mock_fresh):
        svc = MagicMock()
        svc.users.return_value.messages.return_value.modify.return_value.execute.side_effect = OSError("reset")
        label_and_archive(svc, "42", "LBL")
        mock_fresh.assert_called_once()


# ── end-to-end _main ────────────────────────────────────────────────────────────

class TestMain:
    @patch("gmail_substack_archive.get_gmail_service")
    def test_files_substack_and_skips_others(self, mock_get):
        svc, messages = _service(
            list_pages=[{"messages": [{"id": "1"}, {"id": "2"}]}],
            messages_by_id={"1": _msg("1", "writer@substack.com"), "2": _msg("2", "x@mail.substack.com")},
        )
        mock_get.return_value = svc
        processed, failed, skipped = _main(dry_run=False)
        assert (processed, failed, skipped) == (1, 0, 1)

    @patch("gmail_substack_archive.get_gmail_service")
    def test_modifies_only_the_substack_message(self, mock_get):
        svc, messages = _service(
            list_pages=[{"messages": [{"id": "1"}, {"id": "2"}]}],
            messages_by_id={"1": _msg("1", "writer@substack.com"), "2": _msg("2", "x@gmail.com")},
        )
        mock_get.return_value = svc
        _main(dry_run=False)
        assert messages.modify.call_args.kwargs["id"] == "1"

    @patch("gmail_substack_archive.get_gmail_service")
    def test_dry_run_makes_no_modifications(self, mock_get):
        svc, messages = _service(
            list_pages=[{"messages": [{"id": "1"}]}],
            messages_by_id={"1": _msg("1", "writer@substack.com")},
        )
        mock_get.return_value = svc
        processed, _, _ = _main(dry_run=True)
        assert processed == 1
        messages.modify.assert_not_called()

    @patch("gmail_substack_archive.get_gmail_service")
    def test_dry_run_does_not_create_a_label(self, mock_get):
        svc, messages = _service(
            list_pages=[{"messages": [{"id": "1"}]}],
            messages_by_id={"1": _msg("1", "writer@substack.com")},
        )
        mock_get.return_value = svc
        _main(dry_run=True)
        svc.users.return_value.labels.return_value.create.assert_not_called()


# ── auth ────────────────────────────────────────────────────────────────────────

class TestAuth:
    @patch("gmail_substack_archive.build")
    @patch("gmail_substack_archive.Credentials")
    @patch("os.path.exists", return_value=True)
    def test_valid_cached_token_builds_service(self, _exists, mock_creds_cls, mock_build):
        mock_creds_cls.from_authorized_user_file.return_value = MagicMock(valid=True)
        get_gmail_service()
        mock_build.assert_called_once()

    @patch("gmail_substack_archive.Credentials")
    @patch("os.path.exists", return_value=True)
    def test_revoked_token_raises_runtime_error(self, _exists, mock_creds_cls):
        mock_creds = MagicMock(valid=False, expired=True, refresh_token="tok")
        mock_creds.refresh.side_effect = Exception("invalid_grant: Token has been expired or revoked")
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds
        with pytest.raises(RuntimeError, match="revoked"):
            get_gmail_service()

    @patch("gmail_substack_archive.get_gmail_service")
    @patch("os.path.exists", return_value=True)
    @patch("os.remove")
    def test_reauth_removes_token_then_reauths(self, mock_remove, _exists, mock_get):
        reauth()
        mock_remove.assert_called_once_with(TOKEN_FILE)
        mock_get.assert_called_once()


# ── monitor reporting ────────────────────────────────────────────────────────────

class TestReporting:
    @patch("gmail_substack_archive.report_run")
    @patch("gmail_substack_archive._main", return_value=(3, 1, 2))
    def test_reports_success_with_counts(self, _main_mock, mock_report):
        main()
        args = mock_report.call_args.args
        assert args[0] == "gmail_substack_archive" and args[1] == "success"

    @patch("gmail_substack_archive.report_run")
    @patch("gmail_substack_archive._main", side_effect=RuntimeError("boom"))
    def test_reports_crash_and_reraises(self, _main_mock, mock_report):
        with pytest.raises(RuntimeError):
            main()
        assert mock_report.call_args.args[1] == "crashed"
