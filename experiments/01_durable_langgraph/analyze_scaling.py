"""Throughput and scaling efficiency from a scaling.sbatch run (plan 14.2, H6).

Reads ``<run dir>/<layout>-w<N>/f0.json`` written by ``run_f0.py``, where
the layout is ``nodes`` (each worker on its own node) or ``one`` (every
worker on the coordinator's node). Throughput is committed tasks per second
of run wall time; scaling efficiency is throughput(N) / (N x throughput(1))
within one layout. Writes ``scaling.json`` in the run directory and prints a
table; exits non-zero if any run's final state differs from the baseline.

    python experiments/01_durable_langgraph/analyze_scaling.py \\
        experiments/01_durable_langgraph/_runs/<job>
"""

import json
import pathlib
import re
import statistics
import sys


def summary(xs):
    xs = sorted(xs)
    return {"n": len(xs), "median": round(statistics.median(xs), 2),
            "min": xs[0], "max": xs[-1]}


def main():
    run = pathlib.Path(sys.argv[1])
    points = {}
    for f in sorted(run.glob("*-w*/f0.json")):
        layout, n = re.fullmatch(r"(\w+)-w(\d+)", f.parent.name).groups()
        rec = json.loads(f.read_text())
        points[(layout, int(n))] = {
            "ok": rec["ok"],
            "hash_equal": f"{sum(rec['checks']['hash_equal'])}/{len(rec['checks']['hash_equal'])}",
            "commits_per_run": rec["hpx"][0].get("commits"),
            "hpx": summary([h["throughput"] for h in rec["hpx"]]),
            "baseline": summary([b["throughput"] for b in rec["baseline"]]),
            "wall_s": summary([h["wall_s"] for h in rec["hpx"]]),
            "commits_per_worker": rec["commits_per_worker"],
            "args": {k: rec["args"][k] for k in ("width", "loops", "latency_ms", "reps")},
        }
    out = {}
    print("| layout | workers | runs equal | throughput, tasks/s | efficiency | baseline tasks/s |")
    print("|---|---|---|---|---|---|")
    for (layout, n), p in sorted(points.items()):
        one = points.get((layout, 1))
        p["efficiency"] = (round(p["hpx"]["median"] / (n * one["hpx"]["median"]), 3)
                           if one else None)
        out[f"{layout}-w{n}"] = p
        print(f"| {layout} | {n} | {p['hash_equal']} | {p['hpx']['median']} "
              f"[{p['hpx']['min']}, {p['hpx']['max']}] | {p['efficiency']} | "
              f"{p['baseline']['median']} |")
    (run / "scaling.json").write_text(json.dumps(out, indent=1, sort_keys=True))
    bad = [k for k, p in out.items() if not p["ok"]]
    print(json.dumps({"not_ok": bad, "scaling": str(run / "scaling.json")}))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
