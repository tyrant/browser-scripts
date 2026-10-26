from unittest.mock import MagicMock, patch

import pytest

from medium_clap import check_medium_login, clap_post, main
from monitor_client import Terminated


class TestMain:
    @patch("medium_clap.raise_on_sigterm")
    @patch("medium_clap._main")
    def test_installs_sigterm_handler(self, _main, mock_raise_on_sigterm):
        main()
        mock_raise_on_sigterm.assert_called_once()

    @patch("medium_clap.raise_on_sigterm")
    @patch("medium_clap.report_run")
    @patch("medium_clap._main", side_effect=Terminated("Killed by signal 15"))
    def test_termination_reports_crashed(self, _main, mock_report, _raise_on_sigterm):
        with pytest.raises(Terminated):
            main()
        assert mock_report.call_args[0][:2] == ("medium_clap", "crashed")


class TestCheckMediumLogin:
    def _check(self, cookies):
        ctx = MagicMock()
        ctx.cookies.return_value = cookies
        with patch("medium_clap._launch", return_value=ctx):
            return check_medium_login(MagicMock(), "/chrome")

    def test_signed_in_uid_is_logged_in(self):
        assert self._check([{"name": "uid", "value": "6fcc2408f5dd"}]) is True

    def test_logged_out_uid_is_not_logged_in(self):
        assert self._check([{"name": "uid", "value": "lo_446805f24"}]) is False

    def test_missing_uid_is_not_logged_in(self):
        assert self._check([{"name": "sid", "value": "abc"}]) is False

    def test_cookie_read_error_is_not_logged_in(self):
        ctx = MagicMock()
        ctx.cookies.side_effect = RuntimeError("boom")
        with patch("medium_clap._launch", return_value=ctx):
            assert check_medium_login(MagicMock(), "/chrome") is False


class TestClapPost:
    @patch("medium_clap.time.sleep")
    def test_missing_button_warning_includes_page_title(self, _sleep, caplog):
        page = MagicMock()
        page.url = "https://medium.com/p/abc?__cf_chl_rt_tk=x"
        page.title.return_value = "Just a moment..."
        page.wait_for_selector.side_effect = TimeoutError("not found")
        assert clap_post(page, "https://medium.com/p/abc") is False
        assert "(page title: 'Just a moment...')" in caplog.text
