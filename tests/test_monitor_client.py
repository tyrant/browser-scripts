import os
import signal

import pytest

from monitor_client import Terminated, raise_on_sigterm


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
