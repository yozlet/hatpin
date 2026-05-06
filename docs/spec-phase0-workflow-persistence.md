# Specification: Phase 0 — Stage-Level Workflow Persistence

**Status:** Draft  
**Date:** 2026-05-05  
**Parent:** [research-workflow-engines.md](./research-workflow-engines.md) §5 Phase 0  

## 1. Purpose

Validate the **“just add persistence”** hypothesis before evaluating external workflow engines: hatpin can recover from process crashes by serializing workflow state at **stage boundaries** (not mid–tool-call), using **no new runtime dependencies** beyond the standard library.

## 2. Goals

1. **Crash recovery:** If the OS or Python process dies between stages, a subsequent `python -m hatpin implement …` run can resume from the **next** stage with restored `WorkflowContext`, assuming the same issue/work unit.
2. **Baseline for Phase 1+:** Produce a **concrete gap list** (observability, sub-stage durability, concurrency, operational comparison to libraries) informed by a working prototype—not speculation alone.
3. **Secrets hygiene:** Align with [research §10](./research-workflow-engines.md): minimize persisted payload, treat `.hatpin/` as machine-local and sensitive, defer encryption-at-rest.

## 3. Non-goals (explicit)

- **Tool-call-level or LLM-turn-level** replay inside a stage (see research §6).
- **Full deferred stages 12–14** (PR feedback loops) as product-ready features; Phase 0 only ships a **minimal external-event hook** to de-risk “poll file / later webhook” integration.
- **Concurrent workflow runs** or cross-machine coordination.
- **Rich queryable history** (multi-run audit DB, dashboards).
- **Encryption at rest** (documented deferral; optional follow-up issue).

## 4. Functional requirements

### 4.1 Serialized state shape

Persist a single JSON document under the repo working tree (default path: **`<repo-path>/.hatpin/state.json`**), atomically written (write temp + rename).

Minimum fields:

| Field | Type | Description |
| ----- | ---- | ----------- |
| `format_version` | int | Schema version (start at `1`). |
| `issue_key` | string | Stable id for the current run (e.g. `owner/repo#123` or normalized issue URL—implementation picks one canonical form and uses it consistently). |
| `next_stage_index` | int | Index into the **concrete** `stages` list passed to `WorkflowEngine.run` for the **next** stage to execute (0-based). |
| `context` | object | Payload that reconstructs `WorkflowContext`. |

The engine today keeps **`current_idx` only in memory** (`hatpin/engine.py`); persistence **must** record the orchestrator position explicitly—context alone is insufficient when stages can be skipped via `should_run`.

### 4.2 When to write

Persist **after** a stage boundary has been fully resolved and the engine knows the **next** index to run:

- After **`record_stage`**, **`post_fn`**, completion display, and **human gate** (if any) for that stage iteration.
- After applying the transition: **PROCEED** (forward), **escape** (backward jump), or **conditional skip** (`should_run` false).

Do **not** persist mid-stage (inside the LLM tool loop).

**Durability semantics:** If the process dies **during** a stage, the last successful write still reflects the **previous** boundary; on restart the **current** stage runs again. This matches **stage-level** durability and relies on existing idempotent tools (research §6).

### 4.3 When to clear

Remove `state.json` on **terminal** workflow exits so a later CLI invocation does not accidentally resume a finished or abandoned run:

- Normal completion (all stages consumed).
- **BLOCKED** / invalid escape / max iterations / human gate rejection (any path that stops the workflow via `workflow_blocked`-style termination).

**Rationale:** Avoid ambiguous “resume into a failed run” UX in Phase 0; operators can still inspect `workflow.log` and re-run manually.

### 4.4 Resume on startup (CLI)

For `implement`:

1. Compute `issue_key` from parsed issue + repo identity (same inputs used today to seed `context.facts`).
2. If `.hatpin/state.json` exists **and** `issue_key` matches, load context + `next_stage_index` and pass them into the engine.
3. If the file exists but **issue_key** differs, **ignore** the file (treat as stale), log a warning, and start fresh **or** overwrite on first save (implementation chooses one behavior and documents it).

### 4.5 `WorkflowContext` serialization

- **`summaries`:** Required for `build_context_string`; must round-trip.
- **`facts`:** Must round-trip all keys needed for downstream stages. Phase 0 requires values to be **JSON-compatible** (or a documented conversion); if a value cannot be encoded, fail loudly during save with a clear error—silent loss is unacceptable for resume correctness.
- **`tool_logs`:** High risk for **secrets** (arguments/results). Default Phase 0 policy: **omit** `tool_logs` from persisted JSON **or** persist **metadata only** (e.g. tool name + error flag + truncated hash placeholder)—pick one approach in implementation and document it. Tests must assert the chosen policy.

### 4.6 External event prototype (minimal)

Deliver a **small, documented hook** proving “external signal → resume” without implementing stages 12–14:

- Convention: optional marker file under `.hatpin/` (e.g. `resume.flag`) inspected at **stage boundaries** (poll before starting each stage).
- If present, consume it (delete after read) and emit a **single** structured log line (and optional `Display` hook if low-cost).
- No requirement for HTTP webhook in Phase 0; file-based proof is enough.

## 5. Non-functional requirements

- **Dependencies:** Standard library only for persistence (no new packages for JSON/file I/O).
- **Git:** Add `.hatpin/` to `.gitignore` so local state is not committed by default.
- **Documentation:** README (or hatpin README) must warn that `.hatpin/` can contain sensitive/local state and should not be synced blindly.

## 6. Verification

1. **Unit tests:** Round-trip serialization for `WorkflowContext` under the chosen `tool_logs` policy.
2. **Engine/integration tests:** Mechanical stages only—simulate crash by **not** saving mid-stage but asserting save/load of index + context; assert terminal exits delete state.
3. **Manual checklist (document in Phase 0 completion notes):** Kill process between stages → restart → resumes at correct stage.

## 7. Deliverables

1. Working implementation meeting §4–§6.
2. **`docs/phase0-persistence-gaps.md`** (or equivalent section appended to this spec): answers to research Phase 0 questions—adequacy of stage-level recovery, difficulty of naive pause/resume, what an external engine would add, and explicit **secrets/payload** tradeoffs.

## 8. Open points (resolve during implementation)

- Canonical **`issue_key`** string format (must be stable for the same GitHub issue).
- Exact **`facts`** typing strategy if non-JSON objects appear in the wild (coercion vs error).
