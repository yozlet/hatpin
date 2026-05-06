# Phase 1 spike: Huey (Tier A1)

**Branch:** `research/phase1-huey`  
**Artifact:** `hatpin/research/huey_spike.py` + `tests/research/test_huey_spike.py` (requires optional extra `spike-huey`; sync with `uv sync --extra dev --extra spike-huey`).

## What Huey is

Huey is a **task queue** with pluggable storage (Redis, **SQLite**, filesystem, memory). Workers dequeue tasks, execute them (thread/process/greenlet), optionally persist results, and support retries, scheduling, and **pipelines** (`.s()` / `.then()` chains).

It is **not** a first-class workflow/state-machine engine: there is no built-in notion of “stage graph,” “escape hatch,” or “human gate.”

## Architecture fit vs Hatpin (`WorkflowEngine` + `StageRunner`)

| Concern | Hatpin | Huey mapping |
| -------- | ------ | ------------- |
| Linear backbone + escape hatches | `WorkflowEngine` updates `current_idx`; backward jumps when outcome ≠ `PROCEED` and `escape_target` is valid | **No primitive.** Pipelines are **forward-only**. Backward jumps belong in **your orchestrator** (same Python loop Hatpin already uses), optionally enqueueing tasks by name/args. Spike helper `resolve_next_stage_index()` mirrors that decision outside Huey. |
| Mechanical stages | Sync/async functions per stage | `@huey.task()` plain functions — natural fit; use `call_local()` for tests. |
| LLM stage (long async loop) | `asyncio` + `run_agent_turn` | One Huey task can wrap minutes of work; workers run **blocking** code — typically **thread pool**, not asyncio-native execution. An LLM loop would use `asyncio.run()` inside the task or a dedicated async runner — adds coupling and ties up a worker for the whole loop. |
| Two-channel output | Context + display | Huey stores **task return values** in the result store; anything richer is custom (serialize summary/facts yourself). |

**Invasiveness:** Minimal if Huey is used only as **“run this stage later / retry / persist queue”** while Hatpin keeps orchestration. **High** if every stage becomes a distributed task and control flow moves into Huey pipelines — you **replace** `WorkflowEngine` logic rather than augment it.

## Persistence and crash recovery

- **Storage:** `SqliteHuey` keeps queue (and results) in a SQLite file — suitable for “no broker” local/CLI use (`docs/research-workflow-engines.md` Tier A1).
- **Granularity:** Durability is **per enqueued task** (and stored results), not per line of Python inside a task. If the worker dies **mid-task**, typical behavior is **re-run the whole task** on retry (same as other queues) — **no** built-in checkpoint inside an LLM tool-calling loop.
- **Between stages:** If each Hatpin stage is one Huey task, recovery aligns with **stage-level** durability (same tradeoff as DIY JSON persistence in Phase 0).
- **Mid-stage (tool-call-level):** Would require **many small tasks** or a different engine (e.g. DBOS-style steps), not Huey alone.

Spike test `test_sqlite_queue_survives_process_style_reconnect` shows an enqueued task remains in SQLite after opening a new `SqliteHuey` on the same file — i.e. **queue survives restart** until a consumer drains it.

## Operational notes

- Optional dependency weight: **one small package** (`huey`), SQLite stdlib — **no Redis** required for file-backed mode.
- **Workers:** Production use implies running `huey_consumer` (or embedding `Consumer`) alongside the CLI — extra moving parts vs today’s single-process `WorkflowEngine`.
- Huey **does** help if Hatpin later needs **multiple workflow runs**, priorities, or retries **without** adopting a full workflow platform.

## Recommendation (Phase 1 screen)

| Aspect | Verdict |
| ------ | ------- |
| Escape hatches / backward jumps | **Keep** in Hatpin-style orchestrator; Huey does not replace this. |
| Long-running LLM step | **Possible** as one task; **not** asyncio-first — bridge carefully; ties up a worker. |
| Mechanical sync steps | **Good fit** as ordinary tasks or plain functions. |
| Persistence vs Phase 0 JSON | Huey adds **queue + result store + retries**; **same stage-level granularity** if one task ≡ one stage. |
| Cut / keep | **Keep** Huey in mind for **scheduling / retries / multi-issue queues**, not as a drop-in replacement for `WorkflowEngine`. Prefer **Phase 0 file persistence** until concurrent runs or retry semantics justify Huey’s consumer model. |

Next comparison: APScheduler / `transitions` (Tier A1) per `docs/research-workflow-engines.md` Phase 1.

