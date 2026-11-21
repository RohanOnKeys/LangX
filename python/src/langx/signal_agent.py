"""Helper for an island across nodes: signals and file reads on its node.

The failure harness signals workers by PID, which only works on its own
node, and it learns that a worker is up or has left from files the worker
writes. On a shared file system a file written on one node can take tens of
seconds to show up on another, so neither works across nodes. On every node
other than the coordinator's the island runs one of these helpers.

At startup the helper connects to ``--report HOST:PORT`` and sends its own
port. After that each connection carries one request line and gets one
reply line:

* ``SIGNAME PID``: send the signal; reply ``ok`` once it is sent. The caller
  times the request, so no clock on this node is needed.
* ``READ PATH``: reply ``ok <JSON string of the file's text>``, or
  ``missing``. The file is read on this node, where its writer runs.
* ``PORT -``: reply ``ok <port>``, a TCP port that is free on this node. A
  port found free on another node can be taken here.

Errors reply ``error <text>``. Run with
``python -m langx.signal_agent --report HOST:PORT``.
"""

import argparse
import json
import os
import signal
import socket


def handle(line):
    """The reply to one request line."""
    try:
        verb, arg = line.split(maxsplit=1)
        if verb == "PORT":
            with socket.socket() as s:
                s.bind(("", 0))
                return f"ok {s.getsockname()[1]}"
        if verb == "READ":
            try:
                with open(arg) as f:
                    return "ok " + json.dumps(f.read())
            except FileNotFoundError:
                return "missing"
        os.kill(int(arg), getattr(signal, verb))
        return "ok"
    except (ValueError, AttributeError, OSError) as ex:
        return f"error {type(ex).__name__}: {ex}"


def serve(sock):
    """Answer requests on a listening socket until the process is stopped."""
    while True:
        conn, _ = sock.accept()
        with conn:
            line = conn.makefile().readline().strip()
            conn.sendall((handle(line) + "\n").encode())


def call(host, port, line, timeout_s=5.0):
    """Send one request line to the helper at ``host:port``; return the reply."""
    with socket.create_connection((host, port), timeout=timeout_s) as conn:
        conn.sendall((line + "\n").encode())
        return conn.makefile().readline().strip()


def request(host, port, name, pid, timeout_s=5.0):
    """Ask the helper at ``host:port`` to send signal ``name`` to ``pid``."""
    reply = call(host, port, f"{name} {pid}", timeout_s)
    if reply != "ok":
        raise OSError(f"signal {name} to {pid} on {host}: {reply}")


def read(host, port, path, timeout_s=5.0):
    """The text of ``path`` on the helper's node, or None if it is missing."""
    reply = call(host, port, f"READ {path}", timeout_s)
    if reply == "missing":
        return None
    if not reply.startswith("ok "):
        raise OSError(f"read {path} on {host}: {reply}")
    return json.loads(reply[3:])


def free_port(host, port, timeout_s=5.0):
    """A TCP port that is free on the helper's node."""
    reply = call(host, port, "PORT -", timeout_s)
    if not reply.startswith("ok "):
        raise OSError(f"free port on {host}: {reply}")
    return int(reply[3:])


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m langx.signal_agent")
    ap.add_argument("--report", required=True, help="HOST:PORT to send the port to")
    a = ap.parse_args(argv)
    sock = socket.socket()
    sock.bind(("", 0))
    sock.listen(8)
    host, port = a.report.rsplit(":", 1)
    with socket.create_connection((host, int(port)), timeout=30) as conn:
        conn.sendall(json.dumps({"port": sock.getsockname()[1],
                                 "pid": os.getpid()}).encode() + b"\n")
    serve(sock)


if __name__ == "__main__":
    main()
