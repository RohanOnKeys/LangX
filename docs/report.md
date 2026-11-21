# langX report: exactly-once agent steps on HPX

Codes used in this document (full list: [plan, codes](plan.md#codes)):

| Code | Meaning |
| --- | --- |
| F0 | no failure; the HPX run is compared with stock LangGraph |
| F1 | kill before the node starts: SIGKILL the target's worker right after the coordinator picked it |
| F2 | kill mid-node: SIGKILL the target's worker while the node runs |
| F3 | kill after a direct effect: SIGKILL the worker after it wrote a Tier B effect, before it replied |
| F4 | zombie: SIGSTOP the worker until it is detected as failed, then SIGCONT it |
| F5 | multi-kill: SIGKILL 2 of 4 workers in one superstep |
| F6 | kill and replace: SIGKILL a worker, root `force_disconnect`, a new worker joins and takes work |
| F7 | coordinator crash: SIGKILL the coordinator mid-run; a new one resumes from the checkpoint |
| F8 | slow, not dead: SIGSTOP a worker for less than the detection time, then SIGCONT |
| F9 | abandoned dispatch: dispatch timeout shorter than the node; the original result arrives late |
| G1 | gate, correct result: the final state hash equals the stock LangGraph baseline |
| G2 | gate, outbox exactly once: Tier A keys equal the baseline's and no key is applied twice |
| G3 | gate, no stale commit: no result accepted from a worker after the coordinator saw it fenced |
| G4a | gate, run completes within a bound (120 s per run) |
| G5 | gate, detection from HPX only: every fence comes from HPX, never from the harness |
| H1 to H7 | the plan's hypotheses, stated in the table below |
| Tier A | outbox effect: the node returns an intent, the coordinator applies it after the commit fence, keyed; exactly once |
| Tier B | direct effect: the node performs it on the worker; at least once, effectively once only with a `keyed` sink |

**Date:** 2026-11-21. **HPX:** master `cc2be553` for every result below, unless stated.
**Cluster:** Rostam (LSU), partition `medusa`. **Plan:** [`plan.md`](plan.md). **Per-phase
write-ups:** [`results/`](results/). **Reproducing:** [`reproduce.md`](reproduce.md).

## Question

Agent frameworks such as LangGraph checkpoint state between steps but leave the failure of the
processes that run those steps to the user. When a worker dies or freezes mid-step, the step is
re-run, and a side effect it already caused can happen twice. HPX now has native locality
supervision: failure detection, per-epoch fencing and fenced dispatch. langX asks whether that,
plus a small check on the coordinator, is enough to give a real agent framework these properties
(plan 4.1):

> A LangGraph agent whose nodes execute on HPX localities, with HPX supervision fencing plus a
> coordinator-side commit fence, loses no committed step, commits no step twice, and rejects
> commits from a worker whose epoch is fenced. The final agent state equals a failure-free run.
> Effects emitted through the outbox are applied exactly once.

## Answer

Yes, within the stated limits. On Rostam, a LangGraph agent with every node on HPX workers gave the
stock LangGraph final state in every run of every failure scenario: worker kill before and during a
node, kill after a direct effect, a zombie worker, two simultaneous kills, kill with replacement,
coordinator crash with resume from a checkpoint, a short freeze and an abandoned dispatch; 30
trials each on one node and again with workers on four other nodes. No outbox effect was applied
twice, no late or stale result was committed, and every failure was detected by HPX. Direct
effects a worker performs itself are duplicated by a retry unless they carry a key, as the plan
states.

Getting there needed workarounds for gaps in HPX supervision at `cc2be553`. Six were reported
upstream with C++ reproducers ([hpx#7825](https://github.com/TheHPXProject/hpx/issues/7825),
[hpx#7834](https://github.com/TheHPXProject/hpx/issues/7834)), and four of those are fixed on HPX
master, all four by PRs from this work.

## Method

| Part | What it is |
| --- | --- |
| Root | HPX locality 0, does no work; anchors the runtime and serves a membership action |
| Coordinator | Python process and HPX locality; runs the LangGraph graph, whose nodes are proxies |
| Workers | Python processes and HPX localities; run node functions through one generic HPX action under `dispatch_work` |
| Epoch table | the coordinator's record of which (locality, epoch) is live; a fenced worker is never un-fenced |
| Commit fence | accepts a result only if its attempt id, producing (locality, epoch) and HPX admission all check out, and nothing was accepted for that task yet |
| Outbox (Tier A) | effect intents applied after the commit fence, keyed by (run, step, node, index), unique in an SQLite log |
| Test agent | `research_agent`: plan, a parallel fan-out of searches, reduce, critique, loop, finalize; a stub LLM replays recorded responses |
| Harness | one process per trial, a fresh island, one injected fault by signal; the coordinator learns of failures only from HPX |
| Gates | G1 to G5 per trial; detection and recovery latency, overhead, throughput reported |

Every result comes from a script in the repository; [`reproduce.md`](reproduce.md) lists the
command and Rostam job for each.

## Results

| Phase | What was done | Result |
| --- | --- | --- |
| 0 ([`phase0.md`](results/phase0.md)) | spikes on HPX supervision from Python | detection in about three poll windows; in-flight dispatch to a killed worker never returns; a resumed frozen worker still admits work; shutdown abort found (hpx#7825) |
| 1 ([`phase1.md`](results/phase1.md)) | bindings, root, worker, runtime | a dead observer fences a healthy worker; worked around by not joining peers at worker start |
| 2 ([`phase2.md`](results/phase2.md)) | coordinator loss | losing the coordinator, by crash or clean exit, fences every worker it joined; a fenced worker cannot re-init, so recovery restarts the workers (15/15) |
| 3 ([`phase3.md`](results/phase3.md)) | coordinator, commit fence, outbox, stub LLM | F0 equals stock LangGraph, 5/5, one commit per task |
| overhead ([`overhead.md`](results/overhead.md)) | split of one dispatch | about 250 µs per dispatch, a third of it HPX; supervision adds 9 µs; the largest part is the worker's Python hand-off |
| 4 ([`phase4.md`](results/phase4.md)) | failure matrix on one node | F1 to F9 pass G1 to G5, 30/30 each (300 trials) |
| 5 ([`phase5.md`](results/phase5.md)) | measurement across nodes | network adds about 5 µs per dispatch; throughput scales 1 to 4 workers (efficiency 0.986); F1, F2, F4 to F9 pass G1 to G5 30/30 across five nodes; detection is three poll windows at 250, 500 and 1000 ms; a fresh clone reproduced a subset |

### Hypotheses

| ID | Hypothesis | Verdict | Evidence |
| --- | --- | --- | --- |
| H1 | HPX-dispatched nodes give the same final state hash as stock LangGraph | holds | F0 5/5; 180/180 scaling runs |
| H2 | a killed worker is detected and its node re-run on a survivor, no step lost | holds | F1, F2, F5: 30/30 on one node and across nodes |
| H3 | a resumed frozen worker cannot commit a stale result | holds | F4 30/30 twice; 240 late replies dropped, 0 stale commits |
| H4 | Tier A effects are applied exactly once in every failure scenario | holds | G2 in every trial; F7 re-runs of committed tasks kept to one application by the key |
| H5 | failure-free dispatch overhead is small relative to node latency | holds | about 0.24% of a 100 ms node, against a 10% bound |
| H6 | throughput scales with worker count for I/O-bound branches | holds | 11.0, 22.0, 43.5 tasks per second at 1, 2, 4 workers |
| H7 | a coordinator crash is recoverable without a root restart | holds, with a worker restart | F7 30/30 on one node and across nodes; recovery about 9.4 to 9.8 s, mostly starting fresh workers |

## HPX findings and their status

| Finding (HPX `cc2be553`) | Where | Upstream | langX workaround |
| --- | --- | --- | --- |
| `finalize()` leaves `await_terminal` calls pending; a locality stopping right after it aborts | phase 0 | [#7825](https://github.com/TheHPXProject/hpx/issues/7825), fixed by [#7835](https://github.com/TheHPXProject/hpx/pull/7835) (this work) | pause of two poll timeouts after `finalize()` |
| a failed delivery to a dead observer fences a healthy target | phase 1 | #7834, fixed by [#7897](https://github.com/TheHPXProject/hpx/pull/7897) (this work) | workers join no peers at start |
| `force_disconnect` from a late-connecting locality returns `invalid_status` | phase 0 | #7834, fixed by [#7901](https://github.com/TheHPXProject/hpx/pull/7901) (this work) | a root action does it on request |
| `snapshot_peers()` keeps a peer after it is fenced and removed | phase 0 | #7834, fixed by [#7922](https://github.com/TheHPXProject/hpx/pull/7922) (this work) and follow-ups | the coordinator's own epoch table |
| a dispatch in flight to a killed locality never completes or fails | phase 0 | #7834, open | the coordinator abandons it when HPX shows the target fenced |
| `check_admission` and `dispatch_work` admit any epoch with nothing latched | phase 0 | #7834, open | the commit fence checks the epoch |
| losing the coordinator, even by a clean exit, fences every worker it joined | phase 2 | same mechanism as the observer finding; likely changed by #7897, not tested | F7 restarts the workers |
| the coordinator's `hpx::stop()` does not return after it dispatched to an already-dead worker | phases 4, 5 | not reported; candidate fixes on master: [#7865](https://github.com/TheHPXProject/hpx/pull/7865), [#7912](https://github.com/TheHPXProject/hpx/pull/7912), [#8079](https://github.com/TheHPXProject/hpx/pull/8079), [#8112](https://github.com/TheHPXProject/hpx/pull/8112) | the harness bounds the stop at 30 s |

### Re-check on current HPX master

Pending: HPX master `161df7fc6` (2026-11-21), built next to the `cc2be553` install, with the langX
tests, the hpx#7834 reproducers, F1 to F9 (10 trials each) and the phase 2 coordinator-loss spike
re-run on it (Rostam jobs 224617 to 224621). This section is filled in from those jobs.

## Limits

* **Tier B effects** a worker performs itself are at least once: after a kill between effect and
  reply, a naive sink held one duplicate in 30/30 trials, a keyed sink none.
* **The root is out of scope.** Its loss is not recoverable with current HPX.
* **Detection uses HPX's testing hook** to set the poll timeout (250 to 1000 ms here); the HPX
  default is 60 s.
* **F7 needs a worker restart** at `cc2be553`, so its recovery is about 9.4 s on one node.
* **One coordinator at a time;** split-brain coordinators are not tested.
* **No scheduler determinism, no accelerators, no real LLM calls;** the model is replayed from
  recorded responses.
* **Throughput:** a langX worker runs one node at a time, so the in-process LangGraph baseline,
  which runs branches in parallel threads, is about 25 times faster than one worker at 100 ms per
  node.
* **Scope of the measurements:** Rostam, up to five nodes and four workers, waiting (I/O-bound)
  nodes.
* **Reproduction:** a fresh clone reproduced a subset; a reproduction by someone else (plan 21,
  item 3) is still open.

## Where things are

| Item | Path |
| --- | --- |
| langX package | [`python/src/langx/`](../python/src/langx/) |
| Root and membership action | [`hpx_impl/`](../hpx_impl/) |
| Experiment, harness, analysis | [`experiments/01_durable_langgraph/`](../experiments/01_durable_langgraph/) |
| HPX reproducers | [`bench/hpx_repro/`](../bench/hpx_repro/) |
| Rostam setup | [`bench/setup/`](../bench/setup/) |
| Per-phase write-ups and figures | [`results/`](results/) |
