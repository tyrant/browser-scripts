import json
import os
import signal
from unittest.mock import patch

import pytest

from monitor_client import Terminated, raise_on_sigterm, report_run, run_host


@pytest.fixture
def restore_sigterm():
    original = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, original)


class TestRaiseOnSigterm:
    def test_sigterm_raises_terminated(self, restore_sigterm):
        raise_on_sigterm()
        with pytest.raises(Terminated, match="signal 15"):
            os.kill(os.getpid(), signal.SIGTERM)

    def test_terminated_is_an_exception(self):
        assert issubclass(Terminated, Exception)


class TestRunHost:
    @pytest.mark.parametrize("platform,expected", [("darwin", "Mac"), ("linux", "Prod")])
    def test_maps_platform_to_host(self, monkeypatch, platform, expected):
        monkeypatch.setattr("monitor_client.sys.platform", platform)
        assert run_host() == expected


class TestReportRun:
    def test_payload_includes_host(self, monkeypatch):
        monkeypatch.setenv("MONITOR_API_KEY", "key")
        monkeypatch.setattr("monitor_client.run_host", lambda: "Mac")
        with patch("monitor_client.urllib.request.urlopen") as mock_urlopen:
            report_run("medium_clap", "success", 3)
        payload = json.loads(mock_urlopen.call_args[0][0].data)
        assert payload["host"] == "Mac"

    def test_skips_without_api_key(self, monkeypatch):
        monkeypatch.delenv("MONITOR_API_KEY", raising=False)
        with patch("monitor_client.urllib.request.urlopen") as mock_urlopen:
            report_run("medium_clap", "success")
        mock_urlopen.assert_not_called()
