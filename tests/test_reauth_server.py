"""Tests for reauth_server.py."""
import base64
import json
from unittest.mock import MagicMock, patch

from reauth_server import (
    REMOTE,
    SERVER,
    build_remote_script,
    dump_local_cookies,
    filter_session_cookies,
    inject_on_server,
)


class TestFilterSessionCookies:
    def test_keeps_apex_and_dotted_substack_cookies(self):
        cookies = [
            {"name": "substack.sid", "domain": ".substack.com"},
            {"name": "connect.sid", "domain": "substack.com"},
        ]
        assert filter_session_cookies(cookies) == cookies

    def test_drops_cloudflare_and_alb_cookies(self):
        cookies = [
            {"name": "cf_clearance", "domain": ".substack.com"},
            {"name": "__cf_bm", "domain": ".substack.com"},
            {"name": "AWSALBTG", "domain": ".substack.com"},
            {"name": "substack.sid", "domain": ".substack.com"},
        ]
        assert filter_session_cookies(cookies) == [
            {"name": "substack.sid", "domain": ".substack.com"}
        ]

    def test_drops_non_substack_and_subdomain_scoped_cookies(self):
        cookies = [
            {"name": "x", "domain": "author.substack.com"},
            {"name": "y", "domain": ".google.com"},
            {"name": "substack.sid", "domain": ".substack.com"},
        ]
        assert filter_session_cookies(cookies) == [
            {"name": "substack.sid", "domain": ".substack.com"}
        ]


class TestBuildRemoteScript:
    def test_embeds_cookies_and_remote_path(self):
        cookies_b64 = base64.b64encode(json.dumps([{"name": "a"}]).encode()).decode()
        script = build_remote_script(cookies_b64, remote="/srv/scripts")
        assert cookies_b64 in script
        assert 'sys.path.insert(0, "/srv/scripts")' in script
        assert "sys.exit(0 if ok else 1)" in script


class TestDumpLocalCookies:
    @patch("reauth_server.sync_playwright")
    @patch("reauth_server.get_chromium_executable", return_value="/chrome")
    @patch("reauth_server.check_substack_login", return_value=True)
    def test_returns_filtered_cookies_when_logged_in(self, _login, _exe, mock_pw):
        ctx = MagicMock()
        ctx.cookies.return_value = [
            {"name": "substack.sid", "domain": ".substack.com"},
            {"name": "cf_clearance", "domain": ".substack.com"},
        ]
        with patch("reauth_server._launch", return_value=ctx):
            assert dump_local_cookies() == [
                {"name": "substack.sid", "domain": ".substack.com"}
            ]

    @patch("reauth_server.sync_playwright")
    @patch("reauth_server.get_chromium_executable", return_value="/chrome")
    @patch("reauth_server.interactive_login")
    @patch("reauth_server.check_substack_login", side_effect=[False, True])
    def test_runs_interactive_login_when_local_session_stale(
        self, _login, mock_interactive, _exe, mock_pw
    ):
        ctx = MagicMock()
        ctx.cookies.return_value = []
        with patch("reauth_server._launch", return_value=ctx):
            dump_local_cookies()
        mock_interactive.assert_called_once()

    @patch("reauth_server.sync_playwright")
    @patch("reauth_server.get_chromium_executable", return_value="/chrome")
    @patch("reauth_server.interactive_login")
    @patch("reauth_server.check_substack_login", return_value=False)
    def test_aborts_when_login_never_succeeds(
        self, _login, _interactive, _exe, mock_pw
    ):
        import pytest
        with pytest.raises(SystemExit):
            dump_local_cookies()


class TestInjectOnServer:
    @patch("reauth_server.subprocess.run")
    def test_ssh_command_targets_server_and_returns_code(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert inject_on_server([{"name": "substack.sid"}]) == 0
        args, _ = mock_run.call_args
        cmd = args[0]
        assert cmd[0] == "ssh"
        assert cmd[1] == SERVER
        assert f"{REMOTE}/venv/bin/python -" in cmd[2]
