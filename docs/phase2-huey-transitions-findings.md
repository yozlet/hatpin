# Phase 2 (Huey + `transitions`): Spike Findings + Rationale

**Date:** 2026-05-06  
**Spec/plan:** [`docs/phase2-huey-transitions-spec-and-plan.md`](./phase2-huey-transitions-spec-and-plan.md)  
**Spike code:** `hatpin/workflow_spikes/huey_transitions.py`  
**Spike tests:** `tests/hatpin/test_spike_huey_transitions.py`

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

### 2.4 Async stages are executed via `asyncio.run(...)` (for the spike)

**Decision:** stage functions are `async def`, executed inside `run_tick()` using `asyncio.run(...)`.

**Reasoning:** we need at least one async “LLM-like” stage in Phase 2, and the spike is synchronous at the boundary. `asyncio.run(...)` is deterministic and keeps the spike self-contained.

**Caveat:** this will not be safe if `run_tick()` is ever called from within an already-running event loop. If we integrate this pattern into real Hatpin execution, we’ll need a single “blessed” async bridging strategy.

### 2.5 Huey integration: immediate-mode for deterministic tests

**Decision:** `enqueue_tick()` uses Huey + SQLite, but tests run in Huey “immediate” mode (synchronous) rather than spinning up a worker thread/process.

**Reasoning:** the spike’s primary question is “does the architecture make sense?”, not “can we manage a consumer lifecycle inside unit tests.” Immediate-mode keeps tests stable and fast.

**Trade-off:** immediate-mode does not fully validate worker behavior (e.g. retry mechanics, task result semantics, concurrency).

### 2.6 Retry simulation is stage-boundary and persisted

**Decision:** simulate a transient failure by raising `OSError` once when the run is in `coding`, and persist a marker in the checkpoint (`checkpoint["spike"]["failed_once_in_coding"]`) so a retry can succeed.

**Reasoning:** without persisting the “failed once” marker, a crash or re-run would fail forever. Persisting the marker models real-world retry systems: the unit of durability is the checkpoint, so retry logic must be compatible with re-execution.

### 2.7 Pause/resume is represented as a persisted waiting state + flag file

**Decision:** pausing moves into `waiting_external` and blocks further progress until `resume(run_id)` writes a resume flag file which the next tick consumes.

**Reasoning:** this matches the “multi-day wait” shape without requiring the Python process to stay alive. It is also compatible with ADR 0001’s goal: unify “pause after stage” into one conceptual mechanism, even if this spike doesn’t implement the `WorkflowGate` protocol directly.

## 3. Pros / cons of the current spike shape

### Pros

- **Small interface, deep behavior:** `run_tick()` is a narrow surface that hides graph decisions, persistence, and stage execution.
- **Graph is explicit:** back-edges are first-class and testable (no ad hoc `if` ladders).
- **Stage-boundary durability is concrete:** crash-resume semantics are naturally expressed as “rerun current stage.”
- **Separation of concerns:** `transitions` is not the persistence layer; `WorkflowContext` remains Hatpin’s canonical state accumulator.

### Cons / limitations

- **Async bridging is not production-safe:** `asyncio.run(...)` will collide with “already running loop” contexts.
- **Huey worker semantics are not fully validated:** tests do not start a consumer; immediate-mode is a useful approximation, not a proof.
- **Retry handling in immediate-mode is ad hoc:** `enqueue_tick()` retries once manually for `OSError` in immediate mode. In a real worker, we’d want Huey-native retries with backoff.
- **Pause modeling is simplified:** the spike uses a flag file; real waits (PR review, CI completion) need a richer gate condition model.
- **Checkpoint schema is spike-specific:** it is versioned, but not yet tied to Hatpin’s stage list/graph evolution story.

## 4. Future improvements (next iteration)

### 4.1 Run “real” Huey consumer in an integration test

Add one test that:

- enqueues a tick,
- runs a Huey consumer/worker (thread/process) against the same SQLite DB,
- asserts the checkpoint advanced.

This validates the actual durable execution mechanism, not just the API shape.

### 4.2 Adopt ADR 0001 (`WorkflowGate`) explicitly in the spike

Replace the ad hoc pause flag with a minimal `WorkflowGate`-like object that:

- can represent `StdinGate` and `ExternalConditionGate`
- makes “pause reason” and “resume key” part of an explicit protocol

### 4.3 Replace `asyncio.run` with a single blessed async runner strategy

Introduce a helper that:

- detects if a loop is already running
- chooses a safe execution strategy (dedicated thread/loop, or forcing sync stages inside Huey tasks)

### 4.4 Tighten persistence contract to match Phase 0 spec

If/when migrating beyond spike-quality:

- define a canonical `run_id` (aligned to issue key)
- define where checkpoint lives in the repo
- define a fail-closed story for `graph_version` mismatch
- consider storing `pause_reason`/`pause_key` explicitly (currently mixed between `pause_reason` and `checkpoint["pause"]`)

### 4.5 Clearer “unit of retry”

Decide whether “retry” is:

- strictly “rerun the same state id”
- or “re-enqueue tick until a non-retryable outcome is produced”

Then encode that policy in:

- exception taxonomy (retryable vs terminal)
- Huey task configuration (`retries`, backoff)

## 5. Recommendation (preliminary)

**Proceed** to a “real worker” integration slice *if* we can define a robust async bridging approach.

If async bridging becomes complex quickly, consider a split:

- Huey runs **mechanical** stages and gating/scheduling
- async LLM stages run in a separate “agent runner” process/worker that Huey invokes via a stable boundary (subprocess, RPC, or a dedicated async worker)

