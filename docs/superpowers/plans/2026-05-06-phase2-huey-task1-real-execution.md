# Phase 2 Huey Task 1 — Real execution integration test

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `get_spike_huey()` and one integration test that proves `enqueue_tick` enqueues to Huey’s SQLite store and a single `Worker.loop()` advances the spike checkpoint from `planning` to `coding` without `huey.immediate`.

**Architecture:** Reuse the existing `_get_huey()` singleton and `_get_tick_task` registration. The test sets `HATPIN_SPIKE_STATE_DIR` to `tmp_path`, forces `immediate=False`, enqueues one tick, then drives Huey’s worker implementation directly (`consumer.worker_threads[0][0]`) with `initialize()` + `loop()` once — matching Huey 3’s execution path (not `Consumer.loop()`, which is the supervisor).

**Tech Stack:** Python 3.13, Huey 3.x (`SqliteHuey`), pytest, `uv run --extra dev`.

---

## File map

| File | Role |
|------|------|
| [`hatpin/workflow_spikes/huey_transitions.py`](../../hatpin/workflow_spikes/huey_transitions.py) | Add public `get_spike_huey()` delegating to `_get_huey()`. |
| [`tests/hatpin/test_spike_huey_transitions.py`](../../tests/hatpin/test_spike_huey_transitions.py) | Add integration test; optionally switch existing imports from `_get_huey` to `get_spike_huey`. |
| [`docs/phase2-huey-transitions-findings.md`](../../phase2-huey-transitions-findings.md) | Only touch if Approach B fails in CI and you document Approach A fallback (out of scope unless needed). |

---

### Task 1: Integration test (fails until `get_spike_huey` exists)

**Files:**
- Modify: [`tests/hatpin/test_spike_huey_transitions.py`](../../tests/hatpin/test_spike_huey_transitions.py)

- [ ] **Step 1: Append the new test** at the end of the file (after `test_spike_huey_retryable_failure_retries_once_in_immediate_mode`).

```python
def test_spike_huey_enqueue_tick_real_worker_advances_checkpoint(tmp_path, monkeypatch):
    """Non-immediate mode: task is queued and Worker.loop executes run_tick once."""
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import (
        create_run,
        enqueue_tick,
        get_spike_huey,
    )

    run_id = "t1-real-queue"
    create_run(run_id)

    huey = get_spike_huey()
    huey.immediate = False

    enqueue_tick(run_id)

    checkpoint_path = tmp_path / "t1-real-queue.json"
    payload_after_enqueue = json.loads(checkpoint_path.read_text())
    assert payload_after_enqueue["state_id"] == "planning"

    consumer = huey.create_consumer(workers=1, periodic=False)
    worker = consumer.worker_threads[0][0]
    worker.initialize()
    worker.loop()

    payload = json.loads(checkpoint_path.read_text())
    assert payload["state_id"] == "coding"
    assert payload["context"]["summaries"]["planning"] == "planned"
    assert "tool_logs" not in payload["context"]
```

- [ ] **Step 2: Run the new test only — expect failure**

Run:

```bash
cd /Users/yozgrahame/code/hatpin && uv run --extra dev pytest tests/hatpin/test_spike_huey_transitions.py::test_spike_huey_enqueue_tick_real_worker_advances_checkpoint -v
```

Expected: **FAIL** with `ImportError: cannot import name 'get_spike_huey'` (or similar).

---

### Task 2: Public accessor `get_spike_huey()`

**Files:**
- Modify: [`hatpin/workflow_spikes/huey_transitions.py`](../../hatpin/workflow_spikes/huey_transitions.py) — insert immediately **after** the `_get_huey()` function body (after `return huey` / before `_HUEY_BY_DB` is fine, or right after `_get_huey` definition ends at line ~198).

- [ ] **Step 1: Add the function**

Place after `_get_huey()`:

```python
def get_spike_huey():
    """Return the SqliteHuey instance used by :func:`enqueue_tick` for this spike.

    Spike/evaluation only — not a stable Hatpin workflow API. Delegates to the
    same singleton keyed by ``HATPIN_SPIKE_STATE_DIR`` / ``huey.sqlite3`` as
    internal enqueue routing.
    """
    return _get_huey()
```

- [ ] **Step 2: Run the same pytest command**

Run:

```bash
uv run --extra dev pytest tests/hatpin/test_spike_huey_transitions.py::test_spike_huey_enqueue_tick_real_worker_advances_checkpoint -v
```

Expected: **PASS**. If it fails with dequeue empty / checkpoint still `planning`, verify `huey.immediate` is false **before** `enqueue_tick`, and that `Worker.loop()` runs once after a task was enqueued (no extra `initialize` calls needed beyond one).

- [ ] **Step 3: Commit**

```bash
git add hatpin/workflow_spikes/huey_transitions.py tests/hatpin/test_spike_huey_transitions.py
git commit -m "feat(spike): get_spike_huey and real Huey worker integration test"
```

---

### Task 3: Use public accessor in existing Huey tests (consistency)

**Files:**
- Modify: [`tests/hatpin/test_spike_huey_transitions.py`](../../tests/hatpin/test_spike_huey_transitions.py)

- [ ] **Step 1: Replace `_get_huey` with `get_spike_huey`**

In `test_spike_huey_enqueue_tick_immediate_mode`, change imports and usage:

```python
from hatpin.workflow_spikes.huey_transitions import create_run, enqueue_tick, get_spike_huey
# ...
huey = get_spike_huey()
```

In `test_spike_huey_retryable_failure_retries_once_in_immediate_mode`, same replacement.

- [ ] **Step 2: Run full spike test module**

```bash
uv run --extra dev pytest tests/hatpin/test_spike_huey_transitions.py -v
```

Expected: all tests **PASSED**.

- [ ] **Step 3: Commit**

```bash
git add tests/hatpin/test_spike_huey_transitions.py
git commit -m "test(spike): use get_spike_huey in Huey spike tests"
```

---

### Task 4: Full dev test suite sanity check

- [ ] **Step 1: Run tests**

```bash
uv run --extra dev pytest tests/hatpin/ -q
```

Expected: exit code **0** (adjust path to full `tests/` if your PR policy requires the entire suite).

- [ ] **Step 2: (Optional)** If anything fails only in CI, re-read [`docs/superpowers/specs/2026-05-06-phase2-huey-task1-real-execution-design.md`](../specs/2026-05-06-phase2-huey-task1-real-execution-design.md) § Approach A fallback and add a **separate** threaded-consumer test only after documenting why `Worker.loop()` is insufficient in that environment.

---

## Self-review (plan vs spec)

| Spec requirement | Plan coverage |
|------------------|---------------|
| `get_spike_huey()` public, delegates to `_get_huey()` | Task 2 |
| `immediate=False`, enqueue, checkpoint still `planning` | Task 1 test assertions |
| `create_consumer(workers=1, periodic=False)`, no `start()` | Task 1 |
| `worker.initialize()` + `worker.loop()` once | Task 1 |
| Assert `planning` → `coding` + `summaries.planning == "planned"` | Task 1 |
| Non-goals (no retry/async/gate changes) | No tasks touch those |

Placeholder scan: none.

---

## Execution handoff

**Plan complete and saved to** `docs/superpowers/plans/2026-05-06-phase2-huey-task1-real-execution.md`.

**Two execution options:**

1. **Subagent-driven (recommended)** — dispatch a fresh subagent per task, review between tasks; **required sub-skill:** superpowers:subagent-driven-development.

2. **Inline execution** — run tasks in this session with checkpoints; **required sub-skill:** superpowers:executing-plans.

**Which approach?**
