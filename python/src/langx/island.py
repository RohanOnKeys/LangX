"""Launch an island: langx_root and workers, in connect mode.

The calling process is usually the coordinator. Root and workers write ready
files with their PIDs and epochs, and ``<ready>.exit`` files on shutdown.
Used by the integration tests, the phase spikes and the experiment.
"""

import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[3]
HOST = "localhost"


def free_port():
    """Pick a free TCP port on the loopback interface."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_json(path, timeout_s):
    """Return the JSON in ``path`` once it exists, or None after timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            time.sleep(0.05)
    return None


class Island:
    """Root plus ``n_workers`` workers, started one at a time.

    ``preload`` lists the ``module:function`` nodes each worker resolves at
    startup; ``pythonpath`` adds directories where those modules live.

    Across nodes: ``host`` is this node's name, used for the root's address,
    and ``worker_nodes`` lists the Slurm nodes workers go to, in turn; a
    worker on another node is started through ``srun`` inside the current
    allocation, with one ``langx.signal_agent`` per node so that ``signal``
    and ``kill`` reach it. ``workdir`` must then be on a shared file system.
    """

    def __init__(self, workdir, n_workers=2, poll_timeout_ms=500, threads=4,
                 worker_discovery_ms=0, worker_args=(), root_args=(),
                 preload=(), pythonpath=(), env=None, host=HOST,
                 worker_nodes=()):
        self.dir = pathlib.Path(workdir)
        self.host = host
        self.worker_nodes = list(worker_nodes)
        self.nodes = {}
        self.n_workers = n_workers
        self.worker_discovery_ms = worker_discovery_ms
        # Extra worker flags; "{dir}" and "{name}" are filled in per worker.
        self.worker_args = list(worker_args)
        # Extra root flags; "{dir}" is filled in.
        self.root_args = [x.format(dir=workdir) for x in root_args]
        self.preload = list(preload)
        self.pythonpath = [str(x) for x in pythonpath]
        self.extra_env = dict(env or {})
        self.poll_ms = poll_timeout_ms
        self.threads = threads
        self.root_port = free_port()
        self.agas = f"{host}:{self.root_port}"
        self.procs = {}
        self.ready = {}
        self.agents = {}
        self.agent_procs = {}

    def _popen(self, name, cmd, env=None):
        log = open(self.dir / f"{name}.log", "w")
        self.procs[name] = subprocess.Popen(
            cmd, stdout=log, stderr=subprocess.STDOUT, env=env,
            start_new_session=True)

    def start(self):
        """Start the root, then each worker, and wait for them. Returns self."""
        root_bin = os.environ.get("LANGX_ROOT_BIN",
                                  str(REPO / "python" / "build" / "langx_root"))
        self._popen("root", [
            root_bin, f"--hpx:agas={self.agas}", f"--hpx:hpx={self.agas}",
            "--hpx:expect-connecting-localities", "--hpx:threads=2",
            "--hpx:bind=none", "--hpx:ignore-batch-env",
            "--langx-ready-file", str(self.dir / "root.ready"),
            "--langx-stop-file", str(self.dir / "root.stop"),
            "--langx-poll-timeout-ms", str(self.poll_ms), *self.root_args])
        self.ready["root"] = wait_json(self.dir / "root.ready", 30)
        if self.ready["root"] is None:
            raise RuntimeError("root did not start")

        for i in range(1, self.n_workers + 1):
            self.start_worker(f"w{i}")
        return self

    def start_worker(self, name, node=None):
        """Start one worker and wait for its ready file. Returns its info.

        ``node`` places it on that Slurm node; by default the next entry of
        ``worker_nodes``, or this host if there are none.
        """
        if node is None and self.worker_nodes:
            node = self.worker_nodes[len(self.nodes) % len(self.worker_nodes)]
        self.nodes[name] = node
        if node:
            self._agent(node)
        env = dict(os.environ, **self.extra_env)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO / "python" / "src"), *self.pythonpath,
             env.get("PYTHONPATH", "")])
        # A free port on this host is only a guess for another node; the
        # nodes of an exclusive allocation run nothing else.
        prefix = (["srun", "--nodes=1", "--ntasks=1", f"--nodelist={node}",
                   "--overlap", "--export=ALL"] if node else [])
        self._popen(name, [
            *prefix, sys.executable, "-m", "langx.worker", "--agas", self.agas,
            "--hpx", f"{node or self.host}:{free_port()}",
            "--threads", str(self.threads),
            "--poll-timeout-ms", str(self.poll_ms),
            "--discovery-timeout-ms", str(self.worker_discovery_ms),
            *(["--preload", *self.preload] if self.preload else []),
            "--ready-file", str(self.dir / f"{name}.ready"),
            "--stop-file", str(self.dir / f"{name}.stop"),
            *(x.format(dir=self.dir, name=name) for x in self.worker_args)],
            env=env)
        self.ready[name] = self._wait_json(node, self.dir / f"{name}.ready", 60)
        if self.ready[name] is None:
            raise RuntimeError(f"{name} did not start")
        return self.ready[name]

    def _wait_json(self, node, path, timeout_s):
        """``wait_json`` for a file written on ``node``.

        A file another node writes can take tens of seconds to show up here
        on a shared file system, so it is read by that node's helper.
        """
        if not node:
            return wait_json(path, timeout_s)
        from langx.signal_agent import read  # noqa: PLC0415
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            text = read(node, self.agents[node], str(path))
            if text:
                try:
                    return json.loads(text)
                except ValueError:
                    pass   # caught mid-write
            time.sleep(0.05)
        return None

    def workers(self):
        """Worker name -> ready info (locality, epoch, pid)."""
        return {k: v for k, v in self.ready.items() if k != "root"}

    def _agent(self, node):
        """Start the helper on ``node`` once; it reports its port over TCP."""
        if node in self.agents:
            return
        log = open(self.dir / f"agent-{node}.log", "w")
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(REPO / "python" / "src"),
                                             env.get("PYTHONPATH", "")])
        with socket.socket() as listener:
            listener.bind(("", 0))
            listener.listen(1)
            listener.settimeout(60)
            report = f"{self.host}:{listener.getsockname()[1]}"
            self.agent_procs[node] = subprocess.Popen(
                ["srun", "--nodes=1", "--ntasks=1", f"--nodelist={node}",
                 "--overlap", "--export=ALL", sys.executable, "-m",
                 "langx.signal_agent", "--report", report],
                stdout=log, stderr=subprocess.STDOUT, env=env,
                start_new_session=True)
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                raise RuntimeError(f"helper on {node} did not report") from None
            with conn:
                info = json.loads(conn.makefile().readline())
        self.agents[node] = info["port"]

    def signal(self, name, sig):
        """Send ``sig`` to worker ``name``, on whichever node it runs.

        Returns ``(t, uncertainty_s)``: the CLOCK_MONOTONIC time on this node
        at which the signal was sent, and how far off that can be. For a
        worker on another node ``t`` is the midpoint of the request to its
        node's helper, off by at most half the request's round trip.
        """
        node = self.nodes.get(name)
        if not node:
            t = time.monotonic()
            os.kill(self.procs[name].pid, sig)
            return t, 0.0
        from langx.signal_agent import request  # noqa: PLC0415
        t0 = time.monotonic()
        request(node, self.agents[node], sig.name, self.ready[name]["pid"])
        t1 = time.monotonic()
        return (t0 + t1) / 2, (t1 - t0) / 2

    def kill(self, name):
        """SIGKILL one process, on whichever node it runs."""
        self.signal(name, signal.SIGKILL)
        self.procs[name].wait(10)

    def stop_workers(self, timeout_s=20):
        """Ask live workers to leave; return their exit reports."""
        live = [n for n in self.workers() if self.procs[n].poll() is None]
        for n in live:
            if self.nodes.get(n):
                # A stop file would reach the other node late; SIGTERM is
                # the worker's other way to be asked to leave.
                self.signal(n, signal.SIGTERM)
            else:
                (self.dir / f"{n}.stop").touch()
        return {n: self._wait_json(self.nodes.get(n), self.dir / f"{n}.ready.exit",
                                   timeout_s) for n in live}

    def stop_root(self, timeout_s=20):
        """Ask the root to leave; return its exit report."""
        (self.dir / "root.stop").touch()
        return wait_json(self.dir / "root.ready.exit", timeout_s)

    def cleanup(self):
        """Kill anything still running."""
        # srun passes SIGTERM on to a worker on another node; SIGKILL would
        # only end srun and leave the worker running there.
        remote = [p for n, p in self.procs.items()
                  if self.nodes.get(n) and p.poll() is None]
        remote += [p for p in self.agent_procs.values() if p.poll() is None]
        for p in remote:
            p.terminate()
        for p in remote:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                pass
        for p in [*self.procs.values(), *self.agent_procs.values()]:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
