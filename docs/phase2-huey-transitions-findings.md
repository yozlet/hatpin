# Phase 2 (Huey + `transitions`): Spike Findings + Rationale

**Date:** 2026-05-06  
**Spec/plan:** [`docs/phase2-huey-transitions-spec-and-plan.md`](./phase2-huey-transitions-spec-and-plan.md)  
**Spike code:** `hatpin/workflow_spikes/huey_transitions.py`  
**Spike tests:** `tests/hatpin/test_spike_huey_transitions.py`

**Update (2026-05-06):** Future-improvement **§4.1** (“real” Huey execution) is **done**. There is an integration test that sets `huey.immediate = False`, enqueues a tick, runs one `Worker.loop()` from a `create_consumer(...)` instance (without `start()`), and asserts the checkpoint advances `planning` → `coding`. Public `get_spike_huey()` returns the same `SqliteHuey` instance as `enqueue_tick()`. See [`docs/superpowers/specs/2026-05-06-phase2-huey-task1-real-execution-design.md`](./superpowers/specs/2026-05-06-phase2-huey-task1-real-execution-design.md).

## 1. What we built (and why)

This Phase 2 spike intentionally implements the smallest “hybrid” that still exercises the core hypothesis:

- **`transitions` owns control flow**: explicit states and back-edges (“escape hatches”) are modeled in a state machine.
- **Huey owns durability at the stage boundary**: tasks can be queued and retried, with progress checkpointed between stage executions.
- **Hatpin owns the data model**: `WorkflowContext` remains the shared two-channel accumulator (`summaries` + `facts`) rather than being pushed into `transitions` state.

The spike uses a deliberately tiny workflow:

- `planning` → `coding` → `verify` → `done`
- plus `waiting_external` to model “pause until resumed”
- and a back-edge `verify` → `coding`

This keeps the test surface small while still validating:

- cycles/back-edges (escape hatches)
- stage-boundary persistence
- pause/resume shape
- “retry a stage after transient failure” semantics

## 2. Key decisions (and reasoning)

### 2.1 A small public API (test-first)

**Decision:** expose four public entrypoints:

- `create_run(run_id, initial_context=...)`
- `run_tick(run_id)`
- `enqueue_tick(run_id)`
- `resume(run_id)`

**Reasoning:** these are the smallest “public interfaces” that let tests verify observable behavior without coupling to internals (TDD-friendly and refactor-safe).

### 2.2 Persist JSON checkpoint by default (spike-local)

**Decision:** checkpoint is a JSON file under a spike directory:

- default: `.hatpin/spikes/huey_transitions/<run_id>.json`
- override: `HATPIN_SPIKE_STATE_DIR` (tests use a temp directory)

**Reasoning:**

- aligns with Phase 0’s “stage boundary persistence” posture
- easiest to inspect during evaluation
- avoids introducing a second durable store for the spike (Huey already uses SQLite)

**Notable policy:** persisted context includes **only** `summaries` and `facts`; `tool_logs` are intentionally omitted (secrets risk + size).

### 2.3 `transitions` triggers are called on the *model*

**Decision:** call triggers like `model.proceed()` / `model.escape_to_coding()` (not `machine.proceed()`).

**Reasoning:** in `transitions`, triggers are injected onto the **bound model** by default. This is also a nice design constraint: the runner owns sequencing, but the model provides the allowed moves.

### 2.4 Async bridge: `run_coroutine_sync` + bounded `wait_for`

**Decision:** stage functions are `async def`. `run_tick()` runs them through `run_coroutine_sync`, which uses a fresh event loop via `asyncio.run` on threads with no running loop (typical Huey worker), or a short-lived helper thread when `asyncio.get_running_loop()` already succeeds (pytest-asyncio, notebooks). Each stage coroutine is wrapped in `asyncio.wait_for` so execution time is capped.

**Timeout:** `HATPIN_SPIKE_ASYNC_STAGE_TIMEOUT` (seconds, float). Unset or empty → **300s** default. **`0` disables the cap** (unbounded; test/interactive only — a stuck stage can block a worker forever). On expiry, callers see `asyncio.TimeoutError`. Helper-thread `join` uses a small margin after that cap as a backstop; a still-alive thread after that window raises `RuntimeError` (distinct from timeout).

**Caveats:** re-raised exceptions from the helper thread keep tracebacks from the worker thread (acceptable for the spike). **`wait_for` does not cancel blocking work** inside `async def` (e.g. blocking I/O without `await`); stages must stay cooperative-async. Production would still need a richer cancellation/shutdown story.

### 2.5 Huey integration: immediate-mode for most tests + one real-queue test

**Decision:** `enqueue_tick()` uses Huey + SQLite. Most tests still use Huey **immediate** mode (synchronous) so they stay stable and fast without background threads.

**Reasoning:** the spike’s primary question is “does the architecture make sense?”, not “can we manage a long-lived consumer in every unit test.” Immediate-mode keeps the bulk of coverage simple.

**Addition:** one integration test uses **non-immediate** mode: it enqueues a tick, confirms the checkpoint has **not** advanced yet, then drives a single `Worker.loop()` so dequeue → `execute` runs for real (see §4.1).

**Remaining gap:** we still do not spin up `consumer.start()` (full scheduler + worker threads) in CI; threaded runtime behavior is a smaller slice than queue-backed execution of the decorated task.

### 2.6 Retry simulation is stage-boundary and persisted

**Decision:** simulate a transient failure by raising `OSError` once when the run is in `coding`, and persist a marker in the checkpoint (`checkpoint["spike"]["failed_once_in_coding"]`) so a retry can succeed.

**Reasoning:** without persisting the “failed once” marker, a crash or re-run would fail forever. Persisting the marker models real-world retry systems: the unit of durability is the checkpoint, so retry logic must be compatible with re-execution.

### 2.7 Pause/resume is represented as a persisted waiting state + ADR-shaped gates

**Decision:** pausing moves into `waiting_external`. Progress stays blocked until `resume()` signals release through the same resolver path as `run_tick()` (`resolve_gate_for_pause_key` + `resume.flag:<run_id>` / file signal). The spike implements ADR 0001’s `WorkflowGate` protocol in `hatpin/workflow_gate.py` with concrete gates in `hatpin/workflow_spikes/spike_gates.py` (`ExternalFileWorkflowGate`, `StdinWorkflowGate`). When the gate returns `PROCEED`, `checkpoint["pause"]` is cleared before advancing state so disk never implies “still paused” after transition.

**Reasoning:** matches the “multi-day wait” shape without requiring the process to stay alive; stdin vs external share one protocol. `StdinWorkflowGate` is not selectable via persisted `pause_key` in this slice (only `resume.flag:`); wire a prefix later if needed.

**Spike-grade safety:** `run_id` / pause payload segments use `safe_spike_run_segment` (same discipline as checkpoint filenames); resume paths are confined under `HATPIN_SPIKE_STATE_DIR`.

## 3. Pros / cons of the current spike shape

### Pros

- **Small interface, deep behavior:** `run_tick()` is a narrow surface that hides graph decisions, persistence, and stage execution.
- **Graph is explicit:** back-edges are first-class and testable (no ad hoc `if` ladders).
- **Stage-boundary durability is concrete:** crash-resume semantics are naturally expressed as “rerun current stage.”
- **Separation of concerns:** `transitions` is not the persistence layer; `WorkflowContext` remains Hatpin’s canonical state accumulator.

### Cons / limitations

- **Async bridge is spike-grade:** bounded `wait_for` caps *awaitable* work only; blocking calls inside `async def` stages are not made safe. Helper-thread tracebacks are not rewritten for the caller thread.
- **Huey worker semantics are partly validated:** enqueue → SQLite → dequeue → `execute` is covered without immediate mode; full `consumer.start()` / threaded integration is still not the default test path.
- **Retry handling in immediate-mode is ad hoc:** `enqueue_tick()` retries once manually for `OSError` in immediate mode. In a real worker, we’d want Huey-native retries with backoff.
- **Pause modeling is simplified:** external release is still a flag file under the spike directory, wrapped by `WorkflowGate`; real waits (PR review, CI) need richer gate conditions than file polling.
- **Checkpoint schema is spike-specific:** it is versioned, but not yet tied to Hatpin’s stage list/graph evolution story.

## 4. Future improvements (next iteration)

### 4.1 Run “real” Huey execution (not immediate mode) — **done**

Implemented as `test_spike_huey_enqueue_tick_real_worker_advances_checkpoint`: non-immediate `SqliteHuey`, enqueue tick, assert checkpoint still `planning`, then `Worker.initialize()` + one `Worker.loop()` on the worker from `huey.create_consumer(workers=1, periodic=False)` (no `consumer.start()`).

**Optional later:** add a **threaded** consumer test (`consumer.start()` … `stop()`) if we need extra confidence in scheduler + worker lifecycle — not required for the current Task 1 bar.

### 4.2 Adopt ADR 0001 (`WorkflowGate`) explicitly in the spike — **done (prototype)**

The spike now exposes `WorkflowGate` / `GateOutcome` / `GateReleaseNotReady`, file-backed and stdin gates, persisted `pause_key`, and `run_tick` / `resume` routing through one resolver module (see `tests/hatpin/test_workflow_gate.py`).

**Remaining:** extend `pause_key` prefixes beyond `resume.flag:` when wiring stdin or other drivers; align naming with production `Stage` configuration when/if the main engine adopts gates.

### 4.3 Replace `asyncio.run` with a single blessed async runner strategy — **partially done (spike)**

`run_coroutine_sync` implements the loop-detection + helper-thread strategy and now adds **stage-level `asyncio.wait_for`** (configurable via `HATPIN_SPIKE_ASYNC_STAGE_TIMEOUT`). Remaining gaps: cooperative cancellation beyond `wait_for`, and any production-wide policy for timeouts/backoff.

### 4.4 Tighten persistence contract to match Phase 0 spec

If/when migrating beyond spike-quality:

- define a canonical `run_id` (aligned to issue key)
- define where checkpoint lives in the repo
- define a fail-closed story for `graph_version` mismatch
- checkpoint stores `pause_key` / `reason` under `checkpoint["pause"]` when paused; ticks expose `TickOutcome.pause_reason` when still blocked

### 4.5 Clearer “unit of retry”

Decide whether “retry” is:

- strictly “rerun the same state id”
- or “re-enqueue tick until a non-retryable outcome is produced”

Then encode that policy in:

- exception taxonomy (retryable vs terminal)
- Huey task configuration (`retries`, backoff)

## 5. Recommendation (preliminary)

**Proceed** with **Task 2 (async bridging)** as the next gate for treating this spike as safe outside sync/Huey-worker contexts. The “enqueue → queue → worker executes task” slice is now demonstrated in tests; production-shaped async hosting still needs a blessed bridging strategy.

If async bridging becomes complex quickly, consider a split:

- Huey runs **mechanical** stages and gating/scheduling
- async LLM stages run in a separate “agent runner” process/worker that Huey invokes via a stable boundary (subprocess, RPC, or a dedicated async worker)

