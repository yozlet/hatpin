# Phase 1 spike: APScheduler (Tier A1 screen)

**Branch:** `research/phase1-apscheduler`  
**Scope:** Quick library screen only — not merged to main. See `docs/research-workflow-engines.md` Phase 1.

## What APScheduler is

A **job scheduler** (time-based triggers, persistence of *scheduled jobs*, optional async execution via `AsyncIOScheduler`). It is **not** a workflow/state-machine engine: no built-in stages, DAGs, or backward transitions.

## Fit vs hatpin’s model

| Question | Finding |
| -------- | ------- |
| **Escape hatches / backward jumps** | **App-owned.** APScheduler has no notion of stage graphs or `goto`. You keep `current_stage` (or equivalent) in `WorkflowContext` (or a small dataclass) and decide the next transition yourself—same as today’s engine, minus any scheduler help. |
| **Long async “LLM stage”** | **Supported in principle.** `AsyncIOScheduler` runs **native coroutines** as jobs; duration is limited by process lifetime and your own timeouts, not by APScheduler’s API. Multi-turn tool loops remain your code inside one job. |
| **Mechanical stages** | **Trivial.** Plain sync callables are valid jobs. |
| **Two-channel output (summary vs facts)** | **Outside the scheduler.** Persist or pass summaries/facts in application state; APScheduler only fires callables. |
| **Persistence & crash recovery** | **Job-level only.** `SQLAlchemyJobStore` + SQLite persists **scheduled job records** (next run time, pickled job). **No checkpoint inside an in-flight coroutine:** if the process dies mid-job, work done so far inside that job is lost unless **you** serialize state (e.g. same DIY checkpoint as Phase 0). Recovery is “re-fire job” / “resume schedule,” not replay of partial LLM turns. |
| **Pause / resume (hours/days, external events)** | **Scheduling primitives only.** You can pause/resume **schedules** (APScheduler 4 terminology) or avoid firing jobs until an external driver adds them again; long waits are better modeled as **persisted workflow state + poll/webhook/subcommand**, not as scheduler semantics. |
| **Invasiveness** | **Moderate glue, low conceptual fit.** Core hatpin files (`engine.py`, `stage.py`, workflows) stay readable if APScheduler is only used as an optional driver for “run next callable at time T.” Using it as the **source of truth** for workflow control flow would **invert** the architecture (scheduler-first vs stage machine-first). |

## Dependencies (spike)

Optional group `research` in `pyproject.toml`: `apscheduler`, `sqlalchemy` (for SQLite job store). Install: `uv sync --extra dev --extra research`.

## Code touched (minimal)

- `hatpin/research/apscheduler_harness.py` — tiny helpers + `SpikeWorkflowState` for demos.
- `tests/hatpin/test_apscheduler_phase1_spike.py` — vertical-slice tests (skip entire module if extras missing).

## Verdict for Phase 1 comparison table

**Screen out as a primary workflow engine:** APScheduler does not reduce hatpin’s need for an explicit stage machine, escape-hatch logic, or LLM-loop orchestration; it adds scheduling/persistence for **jobs**, not durable **workflow steps** at hatpin’s granularity. Consider it only as a thin “when to run the next hook” layer **if** you already want cron-like or delayed execution; otherwise Phase 0 file-backed `WorkflowContext` + a driver loop is simpler and closer to the domain.

