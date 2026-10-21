from unittest.mock import patch

import pytest

from medium_clap import main
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
