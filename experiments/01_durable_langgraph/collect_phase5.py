"""Curated phase 5 results and figures, from the raw job outputs on Rostam.

Reads the job outputs named by the flags, writes the numbers the write-up
uses to ``results/phase5.json`` next to this script, and draws the figures
into ``docs/results/figures/``. Run from the repository root on Rostam, where
the raw outputs are; copy the two outputs back to commit them.

    python experiments/01_durable_langgraph/collect_phase5.py \\
        --overhead 224570 --scaling 224571 --poll250 224573 --poll1000 224574 \\
        --one-node 223238 --nodes 224576 [--nodes-rerun JOB ...]

``--nodes-rerun`` names jobs that re-ran some scenarios across nodes; their
results replace those of ``--nodes`` for the same scenarios, and both job
numbers stay in the JSON.

Figures need matplotlib (``requirements-plot.txt``); without it only the
JSON is written.
"""

import argparse
import json
import pathlib
import re
import statistics

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
RUNS = HERE / "_runs"
FIGS = REPO / "docs" / "results" / "figures"

LAYERS = ["plain_async", "dispatch_work", "echo", "node_nowatch", "node", "execute"]
LABELS = {"plain_async": "HPX alone", "dispatch_work": "+ supervision",
          "echo": "+ coordinator Python", "node_nowatch": "+ worker Python",
          "node": "+ fence watch", "execute": "+ commit fence"}
SCENARIOS = ["F1", "F2", "F4", "F5", "F6", "F7-sync", "F8", "F9"]

# Reference palette (dataviz skill), light mode: slots 1 and 2, text, surface.
BLUE, ORANGE = "#2a78d6", "#eb6834"
INK, INK2, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"


def overhead(job):
    """Median p50 per layer, same node and across nodes, from the job log."""
    text = (REPO / f"langx_overhead_nodes_{job}.out").read_text()
    out = {"one": {k: [] for k in LAYERS}, "two": {k: [] for k in LAYERS}}
    where = None
    for line in text.splitlines():
        m = re.match(r"== (python|hpx floor) (one|two)-node", line)
        if m:
            where = m[2]
        elif line.startswith("{") and where:
            rec = json.loads(line)
            for k in LAYERS[2:]:
                out[where][k].append(rec[k]["p50_us"])
        elif line.startswith("R: a rtt") and where:
            m = re.search(r"rtt (\w+) n=\d+ p50_us=([\d.]+)", line)
            out[where][m[1]].append(float(m[2]))
    return {w: {k: round(statistics.median(v), 1) for k, v in d.items()}
            for w, d in out.items()}


def scaling(job):
    data = json.loads((RUNS / str(job) / "scaling.json").read_text())
    return {k: {"workers": int(k.split("-w")[1]), "runs_equal": v["hash_equal"],
                "throughput": v["hpx"], "efficiency": v["efficiency"],
                "baseline": v["baseline"]} for k, v in data.items()}


def aggregate(job):
    return json.loads((RUNS / str(job) / "aggregate.json").read_text())


def failures(job):
    keep = ("gates", "detection_ms", "recovery_ms", "trials", "late_replies",
            "trials_with_unsignaled_fence", "exit_paths", "rerun_after_commit",
            "replacement_commits", "false_positive_rate")
    return {t: {k: x[k] for k in keep if k in x} for t, x in aggregate(job).items()}


def poll(jobs):
    out = {}
    for ms, job in jobs.items():
        a = aggregate(job)
        out[ms] = {"F2_detection_ms": a["F2"]["detection_ms"],
                   "F2_recovery_ms": a["F2"]["recovery_ms"],
                   "F8_false_positive_rate": a["F8"].get("false_positive_rate"),
                   "gates_failed": {t: x["gates_failed"] for t, x in a.items()
                                    if x["gates_failed"]}}
    return out


def style(ax, title, ylabel):
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    ax.set_ylabel(ylabel, color=INK2)
    ax.tick_params(colors=INK2)
    ax.grid(axis="y", color="#e4e3df", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c9c8c2")


def figures(res):
    import matplotlib  # noqa: PLC0415
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    FIGS.mkdir(parents=True, exist_ok=True)
    kw = {"facecolor": SURFACE, "dpi": 150}

    # Overhead per layer, same node against across nodes.
    fig, ax = plt.subplots(figsize=(8, 4), **kw)
    x = range(len(LAYERS))
    for i, (w, color, name) in enumerate((("one", BLUE, "same node"),
                                          ("two", ORANGE, "across 2 nodes"))):
        ax.bar([j + (i - 0.5) * 0.38 for j in x],
               [res["overhead"][w][k] for k in LAYERS], width=0.36, color=color,
               label=name, edgecolor=SURFACE, linewidth=1)
    ax.set_xticks(list(x), [LABELS[k] for k in LAYERS], rotation=20, ha="right")
    style(ax, "Dispatch round trip, each layer adds one cost", "median round trip, µs")
    ax.legend(frameon=False, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(FIGS / "phase5_overhead.png", **kw)

    # Throughput against worker count.
    fig, ax = plt.subplots(figsize=(6, 4), **kw)
    for layout, color, name in (("nodes", ORANGE, "each worker on its own node"),
                                ("one", BLUE, "all workers on one node")):
        pts = sorted((v["workers"], v["throughput"]["median"])
                     for k, v in res["scaling"].items() if k.startswith(layout))
        ax.plot(*zip(*pts), color=color, linewidth=2, marker="o", markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2, label=name)
    one = next(v for k, v in res["scaling"].items() if k == "nodes-w1")
    ax.plot([1, 4], [one["throughput"]["median"], 4 * one["throughput"]["median"]],
            color=INK2, linewidth=1, linestyle="--")
    ax.annotate("linear", (4, 4 * one["throughput"]["median"]), color=INK2,
                xytext=(4, 0), textcoords="offset points", va="center")
    ax.set_xticks([1, 2, 4])
    ax.set_xlabel("workers", color=INK2)
    style(ax, "Throughput, no failures", "committed tasks per second")
    ax.legend(frameon=False, labelcolor=INK2, loc="upper left")
    fig.tight_layout()
    fig.savefig(FIGS / "phase5_scaling.png", **kw)

    # Detection against the poll timeout.
    fig, ax = plt.subplots(figsize=(6, 4), **kw)
    pts = sorted((int(ms), v["F2_detection_ms"]["median"]) for ms, v in res["poll"].items())
    ax.plot(*zip(*pts), color=BLUE, linewidth=2, marker="o", markersize=8,
            markeredgecolor=SURFACE, markeredgewidth=2)
    ax.plot([250, 1000], [750, 3000], color=INK2, linewidth=1, linestyle="--")
    ax.annotate("3 × poll timeout", (1000, 3000), color=INK2, xytext=(-8, 8),
                textcoords="offset points", ha="right")
    ax.set_xticks([250, 500, 1000])
    ax.set_xlabel("poll timeout, ms", color=INK2)
    style(ax, "Detection of a killed worker (F2), one node", "median detection, ms")
    fig.tight_layout()
    fig.savefig(FIGS / "phase5_poll.png", **kw)

    # Detection and recovery per scenario, one node against five.
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), **kw)
    for ax, metric, title in ((axes[0], "detection_ms", "Detection"),
                              (axes[1], "recovery_ms", "Recovery")):
        tags = [t for t in SCENARIOS if (res["one_node"].get(t, {}).get(metric)
                                         or res["nodes"].get(t, {}).get(metric))]
        for i, (src, color, name) in enumerate((("one_node", BLUE, "one node"),
                                                ("nodes", ORANGE, "five nodes"))):
            vals = [(res[src].get(t, {}).get(metric) or {}).get("median", 0) / 1000
                    for t in tags]
            ax.bar([j + (i - 0.5) * 0.38 for j in range(len(tags))], vals,
                   width=0.36, color=color, label=name, edgecolor=SURFACE, linewidth=1)
        ax.set_xticks(range(len(tags)), [t.replace("-sync", "") for t in tags])
        style(ax, f"{title}, median of 30 trials", "seconds")
    axes[0].legend(frameon=False, labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(FIGS / "phase5_failures.png", **kw)


def main():
    ap = argparse.ArgumentParser()
    for flag in ("overhead", "scaling", "poll250", "poll1000", "one-node", "nodes"):
        ap.add_argument(f"--{flag}", required=True)
    ap.add_argument("--nodes-rerun", nargs="*", default=[])
    a = ap.parse_args()
    nodes = failures(a.nodes)
    replaced = {}
    for job in a.nodes_rerun:
        for tag, x in failures(job).items():
            replaced[tag] = {"job": job, "first_run": nodes.get(tag, {}).get("gates")}
            nodes[tag] = x
    res = {"jobs": vars(a),
           "overhead": overhead(a.overhead),
           "scaling": scaling(a.scaling),
           "poll": poll({"250": a.poll250, "500": a.one_node, "1000": a.poll1000}),
           "one_node": failures(a.one_node),
           "nodes": nodes,
           "nodes_replaced": replaced}
    out = HERE / "results" / "phase5.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, sort_keys=True))
    print(f"wrote {out.relative_to(REPO)}")
    try:
        figures(res)
        print(f"wrote {FIGS.relative_to(REPO)}/phase5_*.png")
    except ImportError:
        print("matplotlib not installed: no figures (pip install -r requirements-plot.txt)")


if __name__ == "__main__":
    main()
