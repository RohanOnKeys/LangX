"""Signal helper used to reach workers on other nodes, served in-process."""

import pathlib
import signal
import socket
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "python" / "src"))

from langx.signal_agent import request, serve  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")


@pytest.fixture
def agent():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    threading.Thread(target=serve, args=(sock,), daemon=True).start()
    return sock.getsockname()


def test_kill_reaches_the_target(agent):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    request(*agent, "SIGKILL", child.pid)
    assert child.wait(10) == -signal.SIGKILL


def test_stop_and_continue(agent):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        request(*agent, "SIGSTOP", child.pid)
        request(*agent, "SIGCONT", child.pid)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait(10)


def test_errors_are_reported(agent):
    with pytest.raises(OSError, match="AttributeError"):
        request(*agent, "SIGNOPE", 1)
    with pytest.raises(OSError, match="ProcessLookupError|PermissionError"):
        request(*agent, "SIGCONT", 2 ** 22 + 12345)
