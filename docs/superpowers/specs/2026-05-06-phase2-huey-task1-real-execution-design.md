---
title: "Phase 2 Huey+transitions — Task 1: Prove real Huey execution"
date: 2026-05-06
status: draft
---

## Goal

Implement (and test) **Task 1** from [`docs/phase2-huey-transitions-spec-and-plan.md`](../phase2-huey-transitions-spec-and-plan.md):

> Prove real Huey execution (not immediate mode): enqueue a tick into Huey’s SQLite queue and assert a consumer/worker advances the checkpoint.

This is intentionally a **minimal tracer-bullet** proving that Huey can be the durable executor for the hybrid runner, without expanding scope into async-bridging or gate protocol work yet.

## Current state in repo

- Spike module: [`hatpin/workflow_spikes/huey_transitions.py`](../../hatpin/workflow_spikes/huey_transitions.py)
  - `enqueue_tick()` has a deliberate **immediate-mode bypass**:
    - if `huey.immediate` is true, it calls `run_tick()` directly (and implements a small retry loop), rather than exercising a real consumer.
- Tests: [`tests/hatpin/test_spike_huey_transitions.py`](../../tests/hatpin/test_spike_huey_transitions.py)
  - currently proves `enqueue_tick()` only in `huey.immediate = True` mode.

## Prior art from Huey’s own tests (what to emulate)

Huey supports running a consumer programmatically via `Huey.create_consumer(...)`, which is documented as an “advanced testing scenario” API.

- API docs for programmatic consumer (Huey 3.x): `https://huey.readthedocs.io/en/stable/api.html#Huey.create_consumer`
- Upstream Huey test patterns to reference:
  - **Approach A-style (start real worker threads):**
    - `huey/tests/base.py`: `BaseTestCase.consumer_context()` calls `consumer.start()` and later `consumer.stop(graceful=True)`.\n+      - `https://raw.githubusercontent.com/coleifer/huey/master/huey/tests/base.py`
    - `huey/tests/test_storage.py`: `StorageTests.test_consumer_integration()` runs tasks and blocks on `Result.get(blocking=True, timeout=...)` while a background consumer processes the queue.\n+      - `https://raw.githubusercontent.com/coleifer/huey/master/huey/tests/test_storage.py`
  - **Approach B-style (deterministic manual loop):**
    - `huey/tests/test_consumer.py`: `TestConsumerIntegration.work_on_tasks()` calls `worker.loop()` directly to execute queued tasks deterministically.\n+      - `https://raw.githubusercontent.com/coleifer/huey/master/huey/tests/test_consumer.py`

## Design options for Task 1 test

### Approach B (implement now): deterministic “manual worker.loop()”

**What it is**

- Use `SqliteHuey` in **non-immediate** mode.
- Enqueue the spike tick task using `enqueue_tick(run_id)`.
- Create a consumer object via `huey.create_consumer(...)` but **do not** call `consumer.start()`.
- Execute exactly one unit of consumer work by calling `worker.loop()` for the first worker instance, then assert the checkpoint has advanced.

**Why this is the right default**

- Exercises “real Huey execution” (queue → dequeue → deserialize → execute task) without spinning up background threads in pytest.
- Matches Huey’s own deterministic unit/integration testing style (`worker.loop()`), so it is less flaky and faster.
- Keeps the spike aligned with the Phase 2 timebox and “minimal prototype” intent.

**Guardrails**

- The test must assert that `enqueue_tick()` did not short-circuit and advance state synchronously.\n+  Concretely, ensure the checkpoint is still at `planning` after calling `enqueue_tick(run_id)` but before calling `worker.loop()` (or equivalently, assert the queue length increased).

### Approach A (fallback if we hit concurrency/locking weirdness): run the consumer threads

**What it is**

- Create a consumer via `huey.create_consumer(...)`.
- Call `consumer.start()` to spawn scheduler + worker threads.
- Enqueue a tick, then wait until the checkpoint advances (or until a `Result.get(...)` succeeds if we use a return value).
- Stop with `consumer.stop(graceful=True)`.

**Why keep it as a fallback**

- It’s closer to “real” runtime behavior.\n+  Huey itself uses this in its integration-style tests (see the upstream references above).
- It is more timing-sensitive and can become flaky depending on SQLite scheduling/locking and test timeouts.

## Concrete test contract (Task 1)

Add **one** new integration-style test (location: [`tests/hatpin/test_spike_huey_transitions.py`](../../tests/hatpin/test_spike_huey_transitions.py)) that:

- sets `HATPIN_SPIKE_STATE_DIR` to `tmp_path`
- calls `create_run(run_id)`
- sets `huey.immediate = False`
- calls `enqueue_tick(run_id)` and verifies no synchronous state advance happened
- constructs a consumer via `huey.create_consumer(...)`
- calls `worker.loop()` once (Approach B)
- asserts the checkpoint JSON advances from `planning` → `coding`

## Next step

Once this spec is approved, we’ll write the implementation plan and then implement the new test (no other Phase 2 tasks yet).
