# Phase 0 Workflow Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement stage-boundary JSON persistence for `WorkflowContext` + orchestrator index, CLI resume for `implement`, terminal-exit cleanup, a minimal external resume flag hook, and a written gap analysis—no new third-party dependencies.

**Architecture:** Add a small `hatpin/persistence.py` module that owns the on-disk schema (`format_version`, `issue_key`, `next_stage_index`, context payload), atomic writes to `<repo-path>/.hatpin/state.json`, and helpers to build a canonical `issue_key` from the same facts used in `hatpin/__main__.py`. Extend `WorkflowEngine.run` with `start_index: int = 0` and an optional persistence callback or narrow config object invoked after each stage boundary transition (including skips and escapes) to save the **next** index; clear the file on terminal exits and on successful completion. Wire `run_workflow` to load when paths/issue match. Implement `resume.flag` consumption at each stage iteration start when persistence is enabled.

**Tech Stack:** Python 3.13+, `json`, `pathlib`, `tempfile`/`os.replace` for atomic save; existing `hatpin` modules only.

---

## File map

| File | Role |
| ---- | ---- |
| Create: `hatpin/persistence.py` | Schema, `issue_key`, save/load/clear, atomic write, optional `resume.flag` consume |
| Modify: `hatpin/context.py` | Serialization helpers (`to_json_dict` / `from_json_dict`) **or** keep dict conversion in persistence module—single ownership |
| Modify: `hatpin/engine.py` | `start_index`, persistence hooks, terminal clear |
| Modify: `hatpin/__main__.py` | Load/save wiring, pass repo path + issue identity |
| Modify: `.gitignore` | Ignore `.hatpin/` |
| Modify: `hatpin/README.md` | Short `.hatpin/` sensitivity note |
| Create: `tests/hatpin/test_persistence.py` | Round-trip + mismatch + atomic behavior |
| Modify: `tests/hatpin/test_engine.py` | Resume index + persistence callback scenarios (mechanical stages) |
| Create: `docs/phase0-persistence-gaps.md` | Research answers + gap list |

---

### Task 1: Context JSON round-trip (tool_logs policy)

**Files:**
- Modify: `hatpin/context.py` (only if methods live here; otherwise `hatpin/persistence.py` only)
- Create: `tests/hatpin/test_persistence.py`
- Test: `tests/hatpin/test_persistence.py`

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path

import pytest

from hatpin.context import WorkflowContext
from hatpin.persistence import context_to_json_dict, context_from_json_dict


def test_context_round_trip_without_tool_logs():
    ctx = WorkflowContext()
    ctx.summaries["plan"] = "Do the thing"
    ctx.facts["branch_name"] = "feat/foo-1"
    ctx.facts["issue_number"] = 42
    raw = context_to_json_dict(ctx, include_tool_logs=False)
    blob = json.dumps(raw)
    back = context_from_json_dict(json.loads(blob))
    assert back.summaries == ctx.summaries
    assert back.facts == ctx.facts
    assert back.tool_logs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/yozgrahame/code/hatpin && uv run pytest tests/hatpin/test_persistence.py::test_context_round_trip_without_tool_logs -v`

Expected: FAIL (import error or missing symbols)

- [ ] **Step 3: Implement minimal `hatpin/persistence.py` + helpers**

Implement `context_to_json_dict` / `context_from_json_dict` using `dataclasses.asdict` for nested `ToolCallRecord` **only when** `include_tool_logs=True`; when False, omit `tool_logs` from export and restore empty list. Use explicit key names matching `WorkflowContext` fields.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/hatpin/test_persistence.py::test_context_round_trip_without_tool_logs -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hatpin/persistence.py tests/hatpin/test_persistence.py
git commit -m "feat(persistence): JSON round-trip for WorkflowContext without tool_logs"
```

---

### Task 2: Full workflow state save/load + issue_key

**Files:**
- Modify: `hatpin/persistence.py`
- Modify: `tests/hatpin/test_persistence.py`

- [ ] **Step 1: Write failing tests**

```python
from hatpin.persistence import (
    build_issue_key,
    clear_state_file,
    load_workflow_state,
    save_workflow_state,
)

def test_issue_key_stable(tmp_path):
    from hatpin.context import WorkflowContext
    ctx = WorkflowContext()
    ctx.facts["repo"] = "o/r"
    ctx.facts["issue_number"] = 7
    assert build_issue_key(ctx) == build_issue_key(ctx)

def test_load_mismatch_returns_none(tmp_path):
    p = tmp_path / ".hatpin" / "state.json"
    p.parent.mkdir()
    save_workflow_state(
        p,
        issue_key="o/r#1",
        next_stage_index=2,
        context_dict={"summaries": {}, "facts": {}, "tool_logs": []},
        include_tool_logs=False,
    )
    assert load_workflow_state(p, expected_issue_key="o/r#9") is None

def test_atomic_write_visible_only_when_complete(tmp_path):
    path = tmp_path / ".hatpin" / "state.json"
    path.parent.mkdir()
    save_workflow_state(
        path,
        issue_key="x/y#1",
        next_stage_index=0,
        context_dict={"summaries": {}, "facts": {}, "tool_logs": []},
        include_tool_logs=False,
    )
    assert path.is_file()
    clear_state_file(path)
    assert not path.exists()
```

Adjust helper names to match your implementation; keep behaviors: stable key, mismatch → None, clear removes file.

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run pytest tests/hatpin/test_persistence.py -v`

- [ ] **Step 3: Implement `build_issue_key`, `save_workflow_state`, `load_workflow_state`, `clear_state_file`**

- Write to `path.with_suffix(".tmp")` then `os.replace` onto final path.
- `save_workflow_state` writes `format_version: 1` and embeds `context_dict` (already flattened JSON dict).
- `load_workflow_state` validates `format_version`, compares `issue_key`, returns `(next_stage_index, context_from_json_dict(...))` or `None`.

- [ ] **Step 4: Run tests — expect PASS**

- [ ] **Step 5: Commit**

```bash
git add hatpin/persistence.py tests/hatpin/test_persistence.py
git commit -m "feat(persistence): workflow state file with issue_key and atomic write"
```

---

### Task 3: Engine — `start_index` + boundary persistence + terminal clear

**Files:**
- Modify: `hatpin/engine.py`
- Modify: `tests/hatpin/test_engine.py`

- [ ] **Step 1: Write failing test for resume index**

Append to `tests/hatpin/test_engine.py`:

```python
async def test_run_respects_start_index():
    order = []

    def mk(name):
        async def fn(ctx):
            order.append(name)
            return StageResult(
                stage_name=name,
                outcome=StageOutcome.PROCEED,
                summary=name,
            )
        return fn

    stages = [
        Stage(name="a", instruction="", is_mechanical=True, mechanical_fn=mk("a")),
        Stage(name="b", instruction="", is_mechanical=True, mechanical_fn=mk("b")),
    ]

    engine = WorkflowEngine(MagicMock())
    await engine.run(stages, WorkflowContext(), start_index=1)

    assert order == ["b"]
```

- [ ] **Step 2: Run — expect FAIL** (`unexpected keyword`)

Run: `uv run pytest tests/hatpin/test_engine.py::test_run_respects_start_index -v`

- [ ] **Step 3: Add `start_index` to `WorkflowEngine.run`**

Initialize `current_idx = start_index` with validation `0 <= start_index <= len(stages)` (if greater than last index, treat as complete/no-op or clamp—document choice in code; prefer no-op safe behavior).

- [ ] **Step 4: Introduce optional persistence**

Add parameters, e.g. `persistence: WorkflowPersistence | None = None` where `WorkflowPersistence` is a `@dataclass` with:

- `state_path: Path`
- `issue_key: str`
- `repo_root: Path` (for resume flag)
- `include_tool_logs: bool = False`

Methods on a small helper class or functions referenced by engine:

- `persist(next_index: int, context: WorkflowContext)` — calls `save_workflow_state`
- `clear()` — `clear_state_file`
- `maybe_consume_resume_flag()` — if file exists, delete and return True

Call `maybe_consume_resume_flag()` at the **top** of each loop iteration before running a stage when persistence is not None.

After computing the **next** `current_idx` for continuing execution, invoke `persist(...)` with that **next** index (the stage pointer **before** the next loop iteration runs). On successful completion of all stages, `clear()`. On every `return` that represents terminal failure (blocked, max iterations, human gate reject), `clear()`.

**Important:** Skipped stages (`should_run` false) still advance index—persist after skip.

- [ ] **Step 5: Add test double**

Use a `MagicMock` persistence object or tmp_path-backed real persistence to assert `persist` called with expected indices after each boundary (one mechanical test with 2–3 stages).

- [ ] **Step 6: Run full engine tests**

Run: `uv run pytest tests/hatpin/test_engine.py -v`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add hatpin/engine.py tests/hatpin/test_engine.py
git commit -m "feat(engine): stage-boundary persistence hooks and start_index resume"
```

---

### Task 4: CLI wiring + gitignore + README

**Files:**
- Modify: `hatpin/__main__.py`
- Modify: `.gitignore`
- Modify: `hatpin/README.md`

- [ ] **Step 1: Write integration-oriented test (optional) or rely on unit tests**

Prefer a focused test in `tests/hatpin/test_cli.py` if harness exists; otherwise manual checklist only for Phase 0.

- [ ] **Step 2: Implement load path in `run_workflow`**

- Resolve `repo_path` to absolute `Path`.
- Build `issue_key` via `build_issue_key` using the same `context.facts` as today (`repo`, `issue_number`, etc.).
- Attempt `load_workflow_state(state_path, issue_key)`; if result is `(idx, ctx)`, use them; else fresh `WorkflowContext()` + facts seed as today + `start_index=0`.
- Construct `WorkflowPersistence(...)` and pass into `engine.run`.

- [ ] **Step 3: `.gitignore`**

Add:

```
.hatpin/
```

- [ ] **Step 4: README blurb**

Two sentences: `.hatpin/` stores local workflow state; may contain sensitive data; do not commit; caution with cloud-synced folders.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest /Users/yozgrahame/code/hatpin/tests -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add hatpin/__main__.py .gitignore hatpin/README.md tests/hatpin/test_cli.py
git commit -m "feat(cli): resume workflow from .hatpin state; ignore local state"
```

---

### Task 5: Phase 0 gap document

**Files:**
- Create: `docs/phase0-persistence-gaps.md`

- [ ] **Step 1: Draft sections**

Answer explicitly:

1. Does stage-level persistence adequately address crash recovery for hatpin’s idempotent tools?
2. How hard is naive external pause/resume (file flag vs future webhook)?
3. What would an external engine still buy (observability, concurrency, compensation)?
4. What was persisted vs intentionally omitted (`tool_logs`), and residual secret risks?

- [ ] **Step 2: Commit**

```bash
git add docs/phase0-persistence-gaps.md
git commit -m "docs: Phase 0 persistence experiment — gaps and follow-ups"
```

---

## Self-review (plan author)

| Spec section | Task coverage |
| ------------ | ------------- |
| §4.1 Serialized shape | Task 2 |
| §4.2 When to write | Task 3 |
| §4.3 When to clear | Task 3 |
| §4.4 CLI resume | Task 4 |
| §4.5 Context / tool_logs policy | Task 1–2 |
| §4.6 External hook | Task 3 (`resume.flag`) |
| §5 Non-functional | Tasks 2–4 |
| §7 Deliverables | Task 5 |

No TBD steps; engineers must align helper names if refactored—update tests in the same commit.

---

**Plan complete.** Execution options:

1. **Subagent-driven (recommended)** — one subagent per task with review between tasks.  
2. **Inline execution** — run tasks in this session using executing-plans with checkpoints.

Which approach do you want?
