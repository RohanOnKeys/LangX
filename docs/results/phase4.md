# Phase 4 failure harness, F1 to F9, on HPX master (cc2be553)

Acronyms and codes used in this document (full list: [plan, acronyms and codes](../plan.md#acronyms-and-codes)):

| Term | Meaning |
| --- | --- |
| HPX | High Performance ParalleX, the C++ runtime langX runs on |
| ID | identifier |
| PID | process identifier |
| PR | pull request |
| WAL | write-ahead logging, an SQLite journal mode |
| SIGKILL | Unix signal that kills a process at once; it cannot be caught |
| SIGSTOP | Unix signal that freezes a process |
| SIGCONT | Unix signal that resumes a frozen process |
| p95 | 95th percentile |
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
| G4a | gate, run completes within a bound (phase 4: 120 s per run) |
| G4b | recorded, not a gate: how each process exited (clean, timeout, killed by the harness) |
| G5 | gate, detection from HPX only: every fence comes from HPX, never from the harness |
| Tier A | outbox effect: the node returns an intent, the coordinator applies it after the commit fence, keyed; exactly once |
| Tier B | direct effect: the node performs it on the worker; at least once, effectively once only with a `keyed` sink |

**Date:** 2026-11-17. **Cluster:** Rostam, partition `medusa`, one node per job. **HPX:** master
`cc2be553fac9b1f2a372f4277d7c708e70bf7eb4`. **Plan:** [`docs/plan.md`](../plan.md) sections 13,
14 and 16, phase 4.

Code: [`experiments/01_durable_langgraph/`](../../experiments/01_durable_langgraph/)
(`run_exp01.py`, `analyze_exp01.py`, `exp01.sbatch`). Earlier results: [`phase3.md`](phase3.md),
[`overhead.md`](overhead.md).

## Summary

* **Exit criterion met: G1 to G5 pass on one node, 30/30 trials in every scenario.** 300 trials
  over F1 to F9 (F3 in two modes): every run completed, every final state hash equals the stock
  LangGraph baseline, no outbox key was applied twice, and no result was accepted from a worker
  after the coordinator saw it fenced.
* **Kill, freeze, multi-kill and replacement are detected by HPX in about 1.0 to 1.6 s** at a
  500 ms poll timeout, with a spread of a few ms within each scenario. No healthy worker was fenced
  in any trial.
* **Late results never reach the commit fence.** In F4 (zombie) 120 late replies and in F9
  (timeout) 30 arrived after their dispatch had been given up; all were dropped, and no first
  attempt of a target task was committed.
* **Coordinator crash recovery works end to end (F7).** A new coordinator removed the dead one and
  the old workers through the root (`rc = 0`, 30/30), resumed from the SQLite checkpoint on fresh
  workers, re-ran no task that had already been committed, and finished with the baseline hash.
  The root was never restarted.
* **Tier B direct effects behave as the plan states.** After a kill between effect and reply
  (F3), a naive sink holds 1 duplicate in 30/30 trials and a keyed sink 0. Tier A has 0 in both.
* **New HPX behaviour:** after a dispatch to a worker that was already dead (F1), the
  coordinator's `hpx::stop()` does not return, 30/30. When the worker dies mid-node (F2 to F6), the
  coordinator stops cleanly.

## Setup

| Item | Value |
|---|---|
| Topology | `langx_root` (locality 0), 2 workers (4 in F5), coordinator in the trial process, one node |
| Node | medusa04, 24 cores, kernel 5.14.0-611.13.1.el9_7 |
| Python stack | Python 3.12.3, LangGraph 1.2.16, langgraph-checkpoint-sqlite 3.1.2 |
| Detection hook | poll timeout 500 ms on every locality |
| Graph | `research_agent`, width 8, 2 loops, 400 ms search latency (F6: width 16, 3 loops, 200 ms) |
| Trial | own process: baseline for the same run id, fresh island, HPX run with one fault, shutdown |
| Clock | CLOCK_MONOTONIC on one node for every timestamp |

| Job | Purpose |
|---|---|
| 223216 | build, unit and integration tests (9/9 integration, F0 included) |
| 223217 | first smoke run; cancelled after the coordinator stop hang (below) |
| 223218 | smoke run, 2 trials per scenario; F3 failed on a harness bug (below) |
| 223221 | F3 smoke run after the fix |
| 223238 | full run, 30 trials per scenario; all results below |

## Scenarios

The target is the first search task, `search:0`, attempt 1. Signals come from the harness, which
knows the PIDs; the coordinator learns of a failure only from HPX (`Runtime.admitted` turning
False, or a dispatch error).

| ID | Injection, relative to the target's dispatch |
|---|---|
| F1 | SIGKILL its worker right after the coordinator picked it, before the dispatch is sent |
| F2 | SIGKILL its worker at 200 ms, halfway through the node |
| F3 | SIGKILL its worker at 600 ms: after its Tier B effect (at 400 ms), before its reply (at 800 ms) |
| F4 | SIGSTOP at 200 ms; once the coordinator sees the worker fenced, wait 500 ms, then SIGCONT |
| F5 | SIGKILL the workers of `search:0` and `search:1` at 200 ms (2 of 4 workers) |
| F6 | SIGKILL at 100 ms; once fenced, root `force_disconnect`, start a replacement, join it |
| F7 | SIGKILL the coordinator after 5 commits (mid-superstep); kill the old workers, start 2 fresh ones; a new coordinator removes the old localities and resumes by `thread_id` (`sync` durability) |
| F8 | SIGSTOP for 250 ms (half a poll window), starting at 100 ms |
| F9 | dispatch timeout of 100 ms for a 400 ms node |

## Results (job 223238, 30 trials each)

### Gates

| ID | G1 hash | G2 outbox | G3 no stale commit | G4a completed | G5 HPX-only fencing |
|---|---|---|---|---|---|
| F1 | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |
| F2 | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |
| F3 naive | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |
| F3 keyed | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |
| F4 | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |
| F5 | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |
| F6 | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |
| F7 | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |
| F8 | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |
| F9 | 30/30 | 30/30 | 30/30 | 30/30 | 30/30 |

G4a is "completed within the 120 s run timeout". G5 checks that every fence the coordinator
recorded came from HPX state or an HPX dispatch error; the harness never fences a worker.

### Detection and recovery

Detection: the signal to the coordinator seeing the worker fenced. Recovery: the signal to the
commit of the target task on another worker (F9: the target's dispatch to its commit). Median,
p95, [min, max] in ms.

| ID | Detected | Detection | Recovery | Retries per trial |
|---|---|---|---|---|
| F1 | 30/30 | 993.5, 994.5 [991.6, 994.8] | 2003.5, 3204.7 [2003.0, 3204.8] | 4 |
| F2 | 30/30 | 1495.9, 1497.4 [1486.6, 1497.7] | 1901.6, 3102.9 [1896.2, 3106.0] | 4 |
| F3 naive | 30/30 | 1090.0, 1091.3 [1085.3, 1091.4] | 3424.3, 5837.3 [3421.4, 5839.1] | 4 |
| F3 keyed | 30/30 | 1089.2, 1090.9 [1084.0, 1091.2] | 3427.3, 5838.6 [3422.8, 5839.1] | 4 |
| F4 | 30/30 | 1495.2, 1496.6 [1493.1, 1496.9] | 1903.7, 3099.4 [1896.8, 3099.8] | 4 |
| F5 | 30/30, both workers | 1483.8, 1489.0 [1477.1, 1489.5] | 2291.4, 2689.6 [2282.8, 2692.3] | 4 |
| F6 | 30/30 | 1591.9, 1593.0 [1590.5, 1593.6] | 1798.4, 2597.3 [1793.0, 3194.6] | 8 |
| F7 | n/a | n/a | 9390.6, 9488.1 [9327.2, 9491.7] | 0 |
| F8 | 0/30 | not evicted | 301.8, 302.3 [301.1, 302.4] | 0 |
| F9 | n/a | n/a | 2003.4, 2003.8 [2003.1, 2003.9] | 1 |

* Detection is two to three poll windows. It differs between scenarios but hardly within one,
  which suggests it is set by when the signal lands relative to the detection sweep, and the
  injection times are fixed relative to the dispatch. This is an inference; the sweep times were
  not recorded.
* The retries are the tasks that were queued on the dead worker: 4 of 8 searches on 2 workers,
  8 of 16 in F6. Each was retried once, on a survivor.
* Recovery includes the wait behind the survivor's own queued searches, so it is longer than
  detection. Its maximum sits about 1.2 s (F3: 2.4 s) above the median in a few trials; the
  queue position of the retried task on the survivor is the likely cause, not investigated.
* F3 recovery is longer because every search there waits 400 ms more after its Tier B effect.

### Late results

| ID | Late replies dropped | First attempt of the target committed | Stale commits |
|---|---|---|---|
| F4 | 120 (4 per trial) | 0/30 | 0 |
| F9 | 30 (1 per trial) | 0/30 | 0 |
| F8 | 0 | 30/30 (no eviction, so the first attempt is the right one) | 0 |

In F4 the frozen worker had the target and three queued searches. After SIGCONT it finished all
four and their replies arrived; the coordinator had abandoned those dispatches when it saw the
worker fenced, so the replies were dropped at the asyncio bridge and never reached the commit
fence. F9 is the same for a timeout: the original's reply arrived after the retry and was
dropped.

### Effects

| ID | Outbox keys equal baseline | Outbox duplicates | Tier B duplicates (baseline) |
|---|---|---|---|
| F1, F2, F4 to F9 | 30/30 | 0 | n/a |
| F3 naive | 30/30 | 0 | 1 in 30/30 trials (0) |
| F3 keyed | 30/30 | 0 | 0 in 30/30 trials (0) |

The naive duplicate is the killed worker's effect written again by the retry on the survivor:
the documented limit of direct effects (plan 6.6).

### Worker replacement (F6)

| Check | Result |
|---|---|
| Membership: start, after fencing, after replacement | `[0, 1, 2, 3]`, `[0, 1, 2, 3]`, `[0, 1, 3, 4]` |
| Replacement errors | 0/30 |
| Tasks committed by the replacement | 21 per trial |

The killed locality (2) stays in membership after it is fenced and leaves only with the root's
`force_disconnect`; the replacement joins as a new locality (4) and takes work in the same run.

### Coordinator crash (F7)

| Check | Result |
|---|---|
| Commits by the old coordinator, then the new one | 5 and 18 in every trial (23 tasks) |
| Tasks re-run after they were committed | 0 |
| Root `force_disconnect` of the dead coordinator and both old workers | `rc = 0`, 30/30 |
| Root alive throughout | 30/30 |
| New coordinator joined to fresh workers, after the kill | 8970 ms median [8907, 9071] |
| Outbox keys equal baseline, duplicates | 30/30, 0 |

This completes plan 9.3: steps 3 and 5 were open after phase 2, where the dead coordinator stayed
in membership. Almost all of the recovery time is setup (killing the old workers, starting two
fresh ones, connecting, removing the old localities, joining); the first commit follows about
0.4 s after the new coordinator has joined the fresh workers.

### Shutdown (G4b, recorded)

| ID | Coordinator | Workers | Root |
|---|---|---|---|
| F1 | did not stop within 30 s, 30/30 | survivor clean | clean |
| F2 to F6, F8, F9 | clean | survivors clean | clean |
| F7 | old killed, new clean | 58/60 fresh workers clean, 2 without an exit report | clean |

The F1 hang is in the coordinator's `hpx::stop()`: `finalize()` is bounded at 10 s inside
`Runtime.shutdown`, so only the stop accounts for 30 s. F1 is the only
scenario in which a dispatch is sent to a worker that is already dead. HPX PR
[#7865](https://github.com/TheHPXProject/hpx/pull/7865), merged after `cc2be553`, makes a send to a
locality that went away report `locality_was_disconnected` instead of leaving the caller waiting;
whether it removes this hang is not tested.

### Wall time

HPX run over the in-process baseline, median: F1 7.75, F2 7.88, F4 7.87, F5 5.44, F6 9.97, F8 3.90,
F9 4.38; F7 2.94 for the resumed run only. The baseline runs the searches of a loop in parallel
threads, each HPX worker one node at a time (as in phase 3), and a kill leaves one worker for the
rest of the run. For F3 the ratio ranges from 0.66 to 7.84: the baseline's own wall time varied a
lot there, where 8 threads write to one SQLite file; not investigated.

## Harness problems found on the way

| Job | Problem | Fix |
|---|---|---|
| 223217 | a trial waited forever for the coordinator's `hpx::stop()` after F1, 300 s per trial | the coordinator stop is bounded at 30 s and recorded as `stop_hang_30s` |
| 223218 | F3 crashed: 8 parallel searches opened a new Tier B SQLite file at once, and switching it to WAL does not wait on the busy timeout (`database is locked`) | the harness creates the file before the run; `EffectLog` switches to WAL only if the file is not in WAL yet |

## Not established

* any cross-node result; everything here ran on one node;
* other poll timeouts, widths, latencies and worker counts;
* F7 with `async` durability (plan 9.3 asks for it separately);
* F8 with a freeze longer than half a poll window but shorter than detection;
* whether HPX PR #7865 removes the F1 coordinator stop hang;
* why two fresh workers in F7 left no exit report.

## Claims and non-claims

Supported:

> On HPX master `cc2be553`, on one Rostam node, a LangGraph agent whose nodes run on HPX workers
> through the langx coordinator and commit fence finished with the stock LangGraph final state in
> 300/300 trials covering worker kill before and during a node, kill after a direct effect, a
> zombie worker, two simultaneous kills, kill with replacement, coordinator crash with resume from
> a SQLite checkpoint, a short freeze and an abandoned dispatch. No outbox effect was applied
> twice, no late or stale result was committed, and every fence came from HPX.

Not claimed:

* no exactly-once guarantee for direct (Tier B) effects without an idempotency key;
* no performance claim; the wall-time ratios reflect worker count and the injected failures;
* no claim beyond one node and a 500 ms poll timeout.

## Next step

Phase 5: Rostam measurement across nodes, baselines and sweeps.
