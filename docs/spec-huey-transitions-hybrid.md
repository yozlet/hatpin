# Specification: Huey + `transitions` Hybrid Runner

**Status:** Draft  
**Date:** 2026-05-05  
**Motivation:** Combine `transitions` (explicit stage graph with escape hatches) with Huey (durable queued execution + retries + eventual concurrency), while preserving Hatpin’s core properties: stage isolation, deterministic control flow, and two-channel context (`summaries` vs `facts`).

**Inputs:**
- Baseline durability: [`docs/spec-phase0-workflow-persistence.md`](./spec-phase0-workflow-persistence.md)
- Phase 0/1 findings: [`docs/research-workflow-engines-phase0-1-findings.md`](./research-workflow-engines-phase0-1-findings.md)

---

## 1. Goal

Provide a **stage-level durable execution** model that supports:

- Resume after crashes **between** stages (re-run the current stage if crash mid-stage).
- A formal state graph with **first-class backward jumps** (escape hatches).
- Optional task retries with backoff at the stage boundary (queue semantics).
- A future-friendly path to multi-day pause/resume and multiple concurrent runs (without adopting a full platform yet).

---

## 2. Non-goals

- Tool-call-level or LLM-turn-level replay inside a stage.
- A full Temporal/DBOS-style workflow history/journal.
- A generic DSL for arbitrary multi-step “agent programs”.
- Making *every* side effect declarative on day 1; we’ll only require a small set of deterministic mechanical executors.

---

## 3. Conceptual model

### 3.1 Three layers of responsibility

**A) Control flow (graph):** `transitions` encodes what states exist and which outcomes permit moving to which next state.

**B) Execution (worker):** Huey runs a *single unit of work* (“run one stage for one workflow run”) and retries that unit if requested/allowed.

**C) State (durability):** Hatpin persists a minimal checkpoint at stage boundaries:

- `run_id` / `issue_key`
- `state_id` (current stage)
- `WorkflowContext` (or minimal subset)
- metadata (attempt counters, timestamps, versioning)

### 3.2 What is a “stage” in this hybrid?

- A `transitions` **state** corresponds to a Hatpin **stage** (or stage id).
- A Huey **task execution** corresponds to “run the current stage once; record result; transition graph; checkpoint; enqueue next task (if any).”

This preserves the current Hatpin worldview: the “unit of durability” is a stage boundary.

---

## 4. Data model

### 4.1 Identifiers

- **`run_id`**: stable string identifying a workflow run. For GitHub issue workflows this can be `canonical_issue_key(owner/repo, issue_number)` (existing Phase 0 approach), optionally extended later (e.g. include a UUID suffix to support multiple runs per issue).

### 4.2 Persisted checkpoint (stage boundary)

Persist a single JSON document (or SQLite row) per run:

```json
{
  "format_version": 1,
  "run_id": "owner/repo#123",
  "workflow_kind": "issue",
  "state_id": "planning",
  "next_state_id": "coding",
  "attempt": 3,
  "updated_at": "2026-05-05T22:34:00Z",
  "context": {
    "summaries": { "planning": "..." },
    "facts": { "repo_path": "...", "issue_url": "...", "plan": { "...": "..." } }
  }
}
```

Notes:
- `tool_logs` are **omitted by default** (Phase 0 policy). If ever persisted, it must be opt-in and redacted.
- Storing both `state_id` and `next_state_id` is optional; the minimal requirement is “current state” and enough info to compute what to run next. Keeping `next_state_id` can help with debugging and resume UX.

### 4.3 State graph versioning

Persist:
- `graph_version`: a monotonic integer or hash tied to the workflow definition used.

On resume, if `graph_version` mismatches, default behavior should be **fail closed** (BLOCKED) unless a migration rule exists. This avoids resuming into the wrong stage after reordering stages.

---

## 5. Execution model

### 5.1 The “tick” task

Define a Huey task: `run_workflow_tick(run_id: str) -> None`.

Task behavior:

1. Load checkpoint for `run_id` (or create initial checkpoint if none).
2. Instantiate:
   - `WorkflowContext` from checkpoint
   - `transitions` machine with model bound to a small “run model” carrying `state_id` + context
3. Execute the stage implementation for the current `state_id`:
   - mechanical stage (sync)
   - or LLM stage (async loop via Corvidae) executed inside the task using a defined pattern (see §5.3)
4. Convert stage result to an **outcome event** (e.g. `proceed`, `blocked`, `escape(target)`, `need_human_gate`, `wait_external`).
5. Fire the corresponding `transitions` trigger(s) to advance/backtrack.
6. Persist updated checkpoint (atomic write).
7. If the new state is terminal: clear checkpoint and stop.
8. Else: enqueue the next `run_workflow_tick(run_id)` (or schedule it, if we adopt delay/backoff).

### 5.2 Retries

Retries come from two places:

- **Agent/tool level** (inside stage): tool-call retries and repair loops remain in Corvidae (or equivalent).
- **Stage level** (between stages): Huey retries the tick task if it raises a retryable error *or* if the stage result indicates “try again”.

Define a strict policy:

- **Retryable exceptions:** transient network failures, rate limits, temporary GitHub outages.
- **Non-retryable exceptions:** schema mismatch, invalid graph version, invalid escape target, deterministic validation failures.

Huey’s retry mechanism is task-level; it will re-run the whole stage attempt. That matches our durability goal.

### 5.3 Async inside a Huey worker

We need a single blessed pattern to run async stage implementations inside Huey (which often runs normal callables):

- If the Huey worker thread/process is not running an event loop: use `asyncio.run(stage_fn(...))`.
- If an event loop exists (future-proofing): use an internal helper that detects loop state and uses `asyncio.get_event_loop()` + `run_until_complete` only when safe, or schedules coroutines.

Constraint: avoid nested event loops; the runner must be deterministic and testable.

### 5.4 Human gates and external waits

Unify gates as persisted “paused” states:

- **`human_gate`** (immediate): runner writes checkpoint with `pause_reason = "human_gate"` and stops enqueueing ticks until a resume signal exists.
- **External waits** (multi-day): runner writes checkpoint with `pause_reason = "external_condition"` and a small descriptor of what it’s waiting on (e.g. PR review).

Resuming is triggered by:
- a CLI subcommand (`hatpin resume --issue ...`)
- or an external signal bridge (file flag in Phase 0; webhook later)

The key property: no Python process blocks on stdin for multi-day waits.

---

## 6. Side effects policy

### 6.1 Default posture

Stages may have side effects; stage-level replay is acceptable **only** if:

- side-effectful tools are idempotent, or
- stage logic checks “already done” before doing it again, or
- side effects are moved behind deterministic mechanical executors.

### 6.2 Deterministic sub-workflows (optional but recommended)

For high-risk operations, have the agent produce a structured “intent” object (patch, PR metadata, comment plan), and have the runner execute it via a narrow deterministic executor.

Where this breaks down:
- plan/executor drift (stale base branch),
- partial application and compensation,
- DSL overgrowth.

This is a scoped safety tool, not the primary programming model.

---

## 7. Testing strategy

- Unit tests for checkpoint read/write, graph-version mismatch behavior, and runner decisions.
- Integration-style tests for:
  - “tick executes mechanical stage then advances”
  - “retryable exception triggers Huey retry”
  - “pause writes checkpoint and does not enqueue next”
  - “resume signal causes next tick”

Tests must avoid asserting internal callback ordering of `transitions`; verify only visible behavior: state ids, checkpoint contents, and task enqueue decisions.

---

## 8. Open questions

- Should persistence remain **JSON files** (`.hatpin/state.json`) or move to a small SQLite DB per repo (still “no broker”)? Huey may already use SQLite for its queue; do we co-locate, or keep them separate?
- Do we want multiple concurrent runs per issue? If yes, `run_id` cannot be only `owner/repo#n`; it needs a suffix.
- What is the smallest “pause protocol” that supports both `human_gate` and PR-review waits without duplicating UX?

