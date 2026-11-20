"""Signal helper for an island across nodes.

The failure harness signals workers by PID, which only works on its own
node. On every other node the island runs one of these helpers. It listens
on TCP; each connection sends one line ``SIGNAME PID`` and gets back ``ok``
or ``error <text>`` once the signal is sent. The caller times the request,
so the signal lands between its send and its reply: no clock on the remote
node is needed.

Run with ``python -m langx.signal_agent --ready-file PATH``.
"""

import argparse
import json
import os
import signal
import socket


def serve(sock):
    """Answer requests on a listening socket until the process is stopped."""
    while True:
        conn, _ = sock.accept()
        with conn:
            line = conn.makefile().readline().split()
            try:
                name, pid = line
                os.kill(int(pid), getattr(signal, name))
                conn.sendall(b"ok\n")
            except (ValueError, AttributeError, OSError) as ex:
                conn.sendall(f"error {type(ex).__name__}: {ex}\n".encode())


def request(host, port, name, pid, timeout_s=5.0):
    """Ask the helper at ``host:port`` to send signal ``name`` to ``pid``."""
    with socket.create_connection((host, port), timeout=timeout_s) as conn:
        conn.sendall(f"{name} {pid}\n".encode())
        reply = conn.makefile().readline().strip()
    if reply != "ok":
        raise OSError(f"signal {name} to {pid} on {host}: {reply}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m langx.signal_agent")
    ap.add_argument("--ready-file", required=True)
    a = ap.parse_args(argv)
    sock = socket.socket()
    sock.bind(("", 0))
    sock.listen(8)
    tmp = f"{a.ready_file}.tmp"
    with open(tmp, "w") as f:
        json.dump({"host": socket.gethostname(), "port": sock.getsockname()[1],
                   "pid": os.getpid()}, f)
    os.replace(tmp, a.ready_file)
    serve(sock)


if __name__ == "__main__":
    main()
