from unittest.mock import MagicMock, patch

import pytest

from medium_clap import check_medium_login, clap_all, clap_post, is_own_author_url, main
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
        page.locator.return_value.first.count.return_value = 0
        assert clap_post(page, "https://medium.com/p/abc") == "failed"
        assert "(page title: 'Just a moment...')" in caplog.text

    @patch("medium_clap.time.sleep")
    def test_own_post_returns_own_without_looking_for_button(self, _sleep):
        page = MagicMock()
        page.url = "https://mikey-clarke.medium.com/some-post-3a54842688fa"
        page.locator.return_value.first.count.return_value = 1
        page.locator.return_value.first.evaluate.return_value = "https://mikey-clarke.medium.com/?source=byline"
        assert clap_post(page, "https://medium.com/p/3a54842688fa") == "own"
        page.wait_for_selector.assert_not_called()


class TestIsOwnAuthorUrl:
    @pytest.mark.parametrize("url", [
        "https://mikey-clarke.medium.com/?source=post_page---byline",
        "https://medium.com/@mikey-clarke?source=post_page---byline",
        "https://medium.com/@mikey-clarke/",
    ])
    def test_own_author_urls(self, url):
        assert is_own_author_url(url) is True

    @pytest.mark.parametrize("url", [
        None,
        "",
        "https://medium.com/@sadmansakibayon1?source=post_page---byline",
        "https://medium.com/@mikey-clarke-fan",
        "https://notmikey-clarke.medium.com/",
    ])
    def test_other_author_urls(self, url):
        assert is_own_author_url(url) is False


class TestClapAll:
    @patch("medium_clap.time.sleep")
    @patch("medium_clap.record_clapped_url")
    @patch("medium_clap.mark_as_read")
    @patch("medium_clap._launch")
    def test_own_post_is_marked_read_recorded_and_counted(self, _launch, mock_mark, mock_record, _sleep):
        with patch("medium_clap.clap_post", side_effect=["own", "clapped", "failed"]):
            counts = clap_all(MagicMock(), "/chrome", [("m1", "u1", "a"), ("m2", "u2", "b"), ("m3", "u3", "c")], MagicMock())
        assert (counts, [c.args[1] for c in mock_mark.call_args_list], [c.args[0] for c in mock_record.call_args_list]) == (
            (1, 1, 1), ["m1", "m2"], ["u1", "u2"]
        )
