# Spike: Phase 0 workflow persistence

**Branch:** `research/phase0-context-persistence`  
**Date:** 2026-05-05  

This spike validates the “just add persistence” hypothesis from [research-workflow-engines.md](../../research-workflow-engines.md) §5 Phase 0.

## What works

1. **`WorkflowContext` JSON serialization** — `hatpin.persistence.context_to_json_dict` / `context_from_json_dict` round-trip `summaries` and `facts`. **`tool_logs` are omitted by default** (optional `include_tool_logs=True` for tests or controlled exports).
2. **Atomic writes** — `save_workflow_state` writes `<repo>/.hatpin/state.json` via temp file + `os.replace`.
3. **Stage-boundary persistence** — When `WorkflowEngine.run(..., state_repo_path=..., state_issue_key=...)` is used, after each resolved boundary (skip, PROCEED, escape) the engine saves `next_stage_index` plus the context snapshot. **Terminal outcomes clear** the file: successful completion, BLOCKED / no escape, undeclared escape, invalid escape target, human gate rejection, max-iterations guard.
4. **CLI resume** — `python -m hatpin implement` loads state when `canonical_issue_key(repo, issue_number)` matches the file; otherwise starts fresh (stale `issue_key` → warning + ignore).
5. **External resume prototype** — Creating `.hatpin/resume.flag` causes the next stage-boundary poll to delete the file and emit an INFO log (“external resume signal consumed”). No HTTP server in Phase 0.

## Canonical `issue_key`

Format: `{repo}#{issue_number}` (e.g. `owner/name#42`). Same form used for matching persisted runs.

## Gaps vs external workflow engines

| Area | Phase 0 (this spike) | Typical engine (e.g. Temporal, DBOS, Huey) |
| ---- | -------------------- | ------------------------------------------- |
| **Granularity** | Stage boundaries only | Often task/step-level; some offer finer checkpoints |
| **Mid-stage crash** | Re-run current stage from scratch | May replay last step/tool call |
| **Observability** | Single JSON file + `workflow.log` | Dashboards, history DB, tracing |
| **Concurrency** | Single run; one state file per repo path | Queues, workers, isolation per run id |
| **Compensation / saga** | None | Built-in patterns in some engines |
| **Secrets** | Omit `tool_logs` by default; facts must be JSON-serializable | Still a policy problem; engines don’t remove the need for redaction |

## Open questions

1. **Schema evolution** — `format_version` is `1`. What happens when stages are added/reordered while a stale `state.json` exists? Today: index may point at the wrong stage; mitigations could include wiring count or stage-name lists into the file.
2. **Facts typing** — Non–JSON-serializable values in `facts` cause save to **fail loudly** (`ValueError`). Whether to coerce or prune specific keys is product-dependent.
3. **`persist_tool_logs`** — Engine flag exists for emergencies; default remains **off** per §10.
4. **Unified gates** — Stdin `human_gate` vs long-lived external waits (PR review) still need a single abstraction if both coexist ([research §10](../../research-workflow-engines.md)).
5. **Multi-issue / parallel runs** — One file per repo path last-writer-wins; parallel workflows need namespaced paths or a DB.

## Manual verification

See research doc §5 “Manual checklist”: kill the process between stages, restart `implement`, confirm resume at the expected index (not automated in CI).

