# Research Plan: Evaluating Existing Workflow Engines for Hatpin

**Date:** 2026-05-05
**Goal:** Determine whether an existing workflow engine (especially one with durable execution) could replace hatpin's custom implementation, and whether the tradeoffs are worthwhile.

---

## 1. Context: What Hatpin Currently Does

Hatpin is a **semi-deterministic workflow engine** that drives an LLM through a multi-stage GitHub issue implementation workflow. Key characteristics:


| Aspect                | Current Implementation                                                                                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Language**          | Python 3.13+, asyncio                                                                                                                                                                             |
| **Architecture**      | Linear stage backbone with escape hatches (backward jumps)                                                                                                                                        |
| **State**             | In-memory only (`WorkflowContext` dataclass)                                                                                                                                                      |
| **LLM integration**   | Imports Corvidae primitives directly (`LLMClient`, `run_agent_turn`, `ToolRegistry`)                                                                                                              |
| **Stages**            | **Implemented:** 11 stages (mechanical and LLM-driven; tool-calling loop per LLM stage). **Deferred:** stages 12–14 (e.g. respond to PR feedback, close issue) — see `hatpin/workflows/issue.py`. |
| **Tool scoping**      | Each stage gets a curated tool set                                                                                                                                                                |
| **Exit control**      | Three-layer: `stage_complete` tool → exit criteria verification → optional human gate                                                                                                             |
| **Workflow**          | GitHub issue → plan → branch → tests → implement → refactor → commit → docs → PR                                                                                                                  |
| **Durable execution** | **None.** If the process crashes, all state is lost.                                                                                                                                              |
| **Pause/resume**      | **Not implemented** for deferred post-PR work. Stages 12–14 need suspend/resume while waiting on external events (PR review, merge, CI).                                                          |
| **Size**              | ~800 lines of implementation across 11 source files                                                                                                                                               |


### Pain Points That Motivate This Research

1. **No crash recovery.** If the process dies mid-workflow (OOM, network timeout, machine restart), all progress is lost — the user must restart from scratch, which may re-post duplicate comments or create duplicate branches.
2. **No pause/resume for deferred stages.** Stages 12–14 (respond to PR feedback, close issue, etc.) cannot be implemented without suspending and later resuming while waiting for external events (PR review, merge).
3. **No observability into intermediate state.** The `workflow.log` file captures what happened, but there's no queryable history of workflow runs, stage attempts, or retry counts.
4. **No concurrent workflows.** Only one workflow run at a time (single process, single context). Whether parallel runs matter is an open product decision — see §10.
5. **Error handling is ad-hoc.** Exit criteria retries (3 attempts then BLOCKED), escape hatches for recovery, but no systematic retry/backoff for transient failures.

### What Works Well (Must Preserve)

1. **Stage isolation.** LLM sees one stage at a time with a focused prompt and scoped tools. This is the core insight — it prevents context drift.
2. **Deterministic control flow.** The orchestrator (not the LLM) decides stage transitions. LLM signals outcomes; engine maps to transitions.
3. **Mechanical stages.** Simple code runs without LLM involvement (label, commit, gate_docs). Fast, cheap, deterministic.
4. **Two-channel output.** LLM summaries (reasoning) separate from orchestrator-gathered facts (tool I/O, diffs).
5. **Plan artifact.** Early stage produces structured data consumed by later stages, avoiding redundant LLM analysis.
6. **Lightweight.** ~800 lines, no external dependencies beyond Corvidae. Easy to understand and modify.

---

## 2. Research Questions

### Primary

1. Does an existing workflow engine with **durable execution** fit hatpin's architecture (stage isolation, deterministic control flow, LLM tool-calling loops)?
2. Would adopting it **reduce or increase** total complexity?
3. Can it handle the **LLM-in-the-loop** pattern (long-running async operations with tool-calling loops that may take minutes per stage)?

### Secondary

1. How would it affect the **tight Corvidae integration** (direct imports, shared config, shared tools)?
2. What's the **operational overhead** (database, server, separate process)?
3. Is there a **migration path** that preserves the existing workflow definition?
4. Does it enable the **deferred stages (12–14)** that need **pause/resume for external events**?

---

## 3. Candidate Engines to Evaluate

Candidates are grouped by **dependency weight** — the operational cost of adopting them. This is the most important practical dimension for hatpin, which currently has zero external dependencies beyond Corvidae.

### Tier A: Minimal dependencies (evaluate first)

Split by **operational footprint**: true zero extra infrastructure vs. a message broker.

#### Tier A1: No broker, no separate workflow server

Pure-Python options with file-based or embedded persistence — closest to “just run the CLI.”


| Engine                        | Deps to install   | Persistence backend          | Durable?       | Notes                                                                                                                                   |
| ----------------------------- | ----------------- | ---------------------------- | -------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **hatpin + file persistence** | 0 new deps        | JSON/SQLite file             | 🟡 stage-level | Serialize `WorkflowContext` after each stage. Not a library — just a pattern.                                                           |
| **Huey**                      | 1 package (85KB)  | SQLite (built-in) or Redis   | ✅ per-task     | Lightweight task queue. Maps stages to tasks. Has retries, scheduling, pipelines. SQLite backend avoids a broker.                       |
| **APScheduler**               | 1 package (64KB)  | SQLAlchemy, MongoDB, etc.    | 🟡 job-level   | Job scheduling with persistence. Supports async. Not a workflow engine per se, but could manage stage execution with state persistence. |
| **transitions**               | 1 package (112KB) | None built-in (add your own) | 🟡 with glue   | State machine library. Could model hatpin's stages as states and add JSON/SQLite persistence. Escape hatches are transitions.           |


#### Tier A2: Lightweight library, but requires Redis or RabbitMQ

Same “small SDK” spirit as A1, but **not** zero-infrastructure: you must run and operate a broker.


| Engine       | Deps to install   | Persistence backend | Durable?   | Notes                                                                                         |
| ------------ | ----------------- | ------------------- | ---------- | --------------------------------------------------------------------------------------------- |
| **Dramatiq** | 1 package (125KB) | Redis or RabbitMQ   | ✅ per-task | Task queue with middleware for retries. Compare to Huey+SQLite — Dramatiq implies broker ops. |


### Tier B: Requires a database, but no separate server (evaluate second)

These need a database (usually Postgres) but don't require running a separate server process. The "server" is your Python process.


| Engine    | Deps to install    | Requires           | Durable?         | Notes                                                                                                                                     |
| --------- | ------------------ | ------------------ | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **DBOS**  | 11 packages (~8MB) | PostgreSQL         | ✅ step-level     | Decorator-based (`@workflow`, `@step`). Very Pythonic. Postgres is the only external dep. Step-level durability with automatic recovery.  |
| **Redun** | ~15 packages       | Postgres or SQLite | 🟡 caching-based | Recomputation-based — doesn't persist workflow state so much as cache results and intelligently recompute. More suited to data pipelines. |


### Tier C: Requires a separate server (evaluate only if A and B don't work)

These are full platforms. They require running a server process (plus usually a database) and have significant SDK footprints.


| Engine       | Deps to install                                                                             | Server required                                    | Durable?        | Notes                                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------- | -------------------------------------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Temporal** | 3 packages (~14MB)                                                                          | Temporal server (Go binary) + PostgreSQL/Cassandra | ✅ event-sourced | Industry-standard durable execution. Requires running a separate server. Most powerful option but biggest operational leap. |
| **Prefect**  | 80+ packages (~huge; order-of-magnitude — verify with `uv lock` / resolver when evaluating) | Optional (ephemeral mode for dev)                  | ✅ task-level    | Can run without a server locally. Dependency tree is very large (e.g. FastAPI, SQLAlchemy, …).                              |
| **Inngest**  | 10 packages                                                                                 | Inngest server (or cloud)                          | ✅ event-driven  | Event-driven model. Python SDK is newer. Can self-host or use cloud.                                                        |


### Tier D: Likely mismatched (skip unless Tier A/B reveal a gap)


| Engine                       | Why skip                                                                                       |
| ---------------------------- | ---------------------------------------------------------------------------------------------- |
| **Airflow**                  | DAG-based, batch-oriented. No escape hatches. Heavyweight (Flask web server, SQLAlchemy, etc.) |
| **Dagster**                  | Asset-oriented, designed for data pipelines. Wrong abstraction for LLM workflows.              |
| **Hamilton**                 | Functional data pipelines, not durable execution.                                              |
| **Step Functions / AWS SFN** | AWS lock-in, JSON state machine definitions, not Python-native.                                |


### Dependency weight comparison

For context, hatpin's **entire current codebase** is ~800 lines with 0 external dependencies beyond Corvidae:

```
Tier A1 (no broker): hatpin+file, Huey, APScheduler, transitions — +0 to +1 package each
Tier A2: Dramatiq     +1 package (+125KB) + Redis or RabbitMQ broker
DBOS:                  +11 packages (~8MB)     ← moderate, needs Postgres
Temporal SDK:          +3 packages (~14MB)     ← moderate SDK, heavy server
Prefect:               80+ packages (verify)   ← heavyweight resolver tree
Inngest:               +10 packages            ← moderate, needs server
```

---

## 4. Evaluation Criteria

For each candidate, evaluate against these dimensions (score 1-5):

### 4.1 Architecture Fit

- **Stage isolation**: Can each "stage" be an independent unit of work with its own prompt/tool set? Or does the engine force a DAG where all steps are visible?
- **LLM-in-the-loop**: Can a single "step" run an async tool-calling loop that may last minutes (multiple LLM turns, tool dispatches)? Or does the engine assume steps are short-lived functions?
- **Escape hatches**: Can the engine express backward jumps (goto an earlier stage)? Most workflow engines assume forward-only DAGs.
- **Mechanical stages**: Can some stages be simple code (no LLM, no await) while others are complex async loops?

### 4.2 Durable Execution

- **Crash recovery**: If the process dies mid-stage, can it resume from the last completed stage? From the last completed tool call within a stage?
- **Pause/resume**: Can execution be suspended (e.g., "wait for PR review") and resumed hours/days later when an external event arrives?
- **Event sourcing / replay**: Does the engine record inputs/outputs of each step? Can you inspect the history?
- **Saga / compensation**: If a stage partially fails (e.g., committed code but PR creation failed), does the engine support rollback or compensation?

### 4.3 Integration Cost

- **Corvidae coupling**: Hatpin imports `LLMClient`, `run_agent_turn`, `ToolRegistry`, `dispatch_tool_call` directly. How much adapter code is needed?
- **Python asyncio compatibility**: Does the engine work with `asyncio` natively? Or does it use threads/processes?
- **Config sharing**: Can the engine read `agent.yaml` and share config with Corvidae?

### 4.4 Operational Overhead

- **Dependencies**: How many new packages? Database requirement? Separate server?
- **Local development**: Can you run workflows locally without infrastructure?
- **Deployment complexity**: What's needed in production vs. what hatpin needs now (a single CLI command)?

### 4.5 Developer Experience

- **Migration effort**: How much of the existing ~800 lines can be preserved? How much must be rewritten?
- **Debugging**: Can you step through a workflow, inspect intermediate state, replay failed stages?
- **Testing**: Can you unit test individual stages easily?

### 4.6 Human gates vs external pause

Hatpin already supports `**human_gate`** (pause before proceeding). Deferred stages 12–14 need a different shape of pause: **wait hours/days for an external event** (PR review, merge), then resume.

For each candidate, clarify:

- Does the engine subsume **both** (gate + external wait), or only one?
- Would adopting an engine **duplicate** pause semantics (two ways to block) unless `human_gate` is mapped cleanly onto one model?

### 4.7 Persisted state and secrets

Once `WorkflowContext` (or message history) is written to disk or a DB, evaluate:

- Which serialized fields could contain **secrets or tokens** (tool payloads, env echoes, API responses)?
- Should persisted snapshots **exclude or redact** raw tool transcripts by default?
- Is **encryption at rest** or a defined data-retention policy required for your threat model?

---

## 5. Evaluation Plan

### Phase 0: Test the "just add persistence" hypothesis (1-2 hours)

Before evaluating any external library, test whether the simplest possible approach works:

1. Add a `save()` / `load()` method to `WorkflowContext` that serializes to JSON
2. After each stage completes, persist the context to a file (e.g. `.hatpin/state.json`)
3. On startup, check for an existing state file and resume from the last completed stage
4. For "pause/resume" (PR review, etc.), write a simple event file that the workflow polls or watches

**Why this first:** Hatpin's tools are already idempotent (comment dedup, idempotent label, branch recreation). Stage-level crash recovery may be "good enough" without any external library. If it is, the question becomes: what additional value does an external engine provide?

**Questions to answer:**

- Does stage-level persistence solve the crash recovery problem adequately?
- How hard is pause/resume to implement naively? (A webhook endpoint + state file?)
- What's missing that an external engine would provide? (Observability? Concurrency? Saga compensation?)
- What goes into the persisted blob, and what must be **redacted or omitted** so replay does not leak secrets?

**Output:** Working prototype with `save()`/`load()` + a clear list of remaining gaps.

### Phase 1: Quick screen of Tier A libraries (2-3 hours)

Screen **Huey**, **APScheduler**, and **transitions** (Tier **A1** — no broker): ~45 minutes each. Optionally add **Dramatiq** (Tier **A2**) only if operating Redis/RabbitMQ is acceptable — compare against Huey with SQLite.

1. Read the docs and core concepts
2. Skim the Python API
3. Answer: **Can it express hatpin's stage model?** Specifically:
  - Escape hatches (backward jumps to earlier stages)?
  - LLM tool-calling loops (a single "task" that runs for minutes with multiple async turns)?
  - Mechanical stages (simple sync code)?
  - Two-channel output (summaries + facts)?
4. Answer: **What does persistence look like?** Can it recover from a crash mid-stage? Only between stages?
5. Answer: **How invasive is it?** Can hatpin's `engine.py`, `stage.py`, and tool definitions stay mostly intact?

**Output:** A comparison table. Cut anything that can't handle escape hatches or requires more than trivial adapter code.

### Phase 2: Deep dive on best candidate (2-4 hours)

Take the best candidate from Phase 1 (or Phase 0's simple persistence if no library is clearly better):

1. **Write a prototype** of hatpin's core workflow (stages 1-5) using the chosen approach
2. **Test crash recovery**: Kill the process mid-workflow, restart, verify it resumes
3. **Test pause/resume**: Simulate the PR review wait pattern
4. **Measure integration cost**: Lines of adapter code. How much existing code is preserved?
5. **Evaluate testing**: Can individual stages still be unit tested in isolation?

Only proceed to Tier B (DBOS) or Tier C (Temporal) if Tier A doesn't have a viable candidate.

### Phase 3: Decision (30 minutes)

Compare the chosen approach against:

- **Complexity delta**: Does it add more code/concepts than it removes?
- **Operational cost**: Any new infrastructure? Or still just `python -m hatpin`?
- **Migration risk**: How much working code must change?
- **Future value**: Does it enable deferred stages 12–14, richer observability, and — **if** parallel runs are a goal (§10) — concurrent workflows?

---

## 6. Key Architectural Question: Durability Granularity

There's a fundamental tension in hatpin's architecture that will determine whether any external library fits:

**Within a single LLM stage**, hatpin runs a multi-turn tool-calling loop (potentially 20 turns). Each turn is:

1. Send messages to LLM
2. Receive response with tool calls
3. Dispatch tool calls
4. Append results to messages
5. Loop until `stage_complete` is called

**Question:** What level of durability does hatpin actually need?


| Level               | What it means                                            | Complexity cost  | How to get it                                                                    |
| ------------------- | -------------------------------------------------------- | ---------------- | -------------------------------------------------------------------------------- |
| **Stage-level**     | Resume from the start of the current stage after a crash | Low (~100 lines) | Serialize `WorkflowContext` to JSON after each stage. Hatpin can do this itself. |
| **Tool-call-level** | Resume from the last completed tool call within a stage  | Medium           | Needs a persistence library or significant new code                              |
| **LLM-turn-level**  | Resume from the last LLM response                        | High             | Needs full message history persistence                                           |


Hatpin's stages are designed to be **idempotent in aggregate** (the comment tool deduplicates, the label tool is idempotent, branches can be recreated). This means **stage-level durability** may be sufficient — if a stage crashes, re-running it from scratch is acceptable because the tools handle duplicate calls gracefully.

Most external engines operate at the **step/task level** — their "step" maps to hatpin's "stage". So they provide the same granularity as the DIY JSON approach, plus infrastructure for managing it.

**The core question becomes:** Is the infrastructure worth it for what it provides over a JSON file?

- **If stage-level is enough** → DIY JSON persistence or a lightweight library like Huey/transitions
- **If tool-call-level is needed** → Huey (task per tool call within a stage) or DBOS (step-level durability)
- **If you need rich observability/concurrency** → DBOS or Temporal

---

## 7. Decision Matrix Template

Fill in during evaluation. The key question: does any option provide meaningful value over Phase 0 (just adding persistence)?


| Criterion                       | Hatpin (current) | + file persistence | Huey | transitions | APScheduler | DBOS | Temporal |
| ------------------------------- | ---------------- | ------------------ | ---- | ----------- | ----------- | ---- | -------- |
| Durable execution               | ❌                | 🟡 stage-level     |      |             |             |      |          |
| Pause/resume (external events)  | ❌                | 🟡 DIY             |      |             |             |      |          |
| Escape hatches (backward jumps) | ✅                | ✅                  |      |             |             |      |          |
| LLM tool-calling loop           | ✅ native         | ✅ native           |      |             |             |      |          |
| Mechanical stages               | ✅                | ✅                  |      |             |             |      |          |
| New packages                    | 0                | 0                  | 1    | 1           | 1           | 11   | 3+server |
| External server required        | ✅ none           | ✅ none             |      |             |             |      |          |
| External database required      | ✅ none           | ✅ none             |      |             |             |      |          |
| Lines of hatpin code preserved  | ~800             | ~780               |      |             |             |      |          |
| Local dev simplicity            | ✅                | ✅                  |      |             |             |      |          |
| Testability per-stage           | ✅                | ✅                  |      |             |             |      |          |


---

## 8. Expected Outcomes

### Most likely: "Just add persistence" (Scenario A)

Stage-level durability (persist `WorkflowContext` to JSON after each stage) plus a simple event mechanism for external events (file watch, webhook, or polling). Hatpin's tools are already idempotent, so re-running a crashed stage is safe.

**Effort:** ~100-200 lines of new code. No new dependencies. Preserves the entire existing architecture.

**This solves:** Crash recovery, basic pause/resume.

**This doesn't solve:** Rich observability, concurrent workflows, sub-stage checkpointing.

### Possible: "Huey or transitions" (Scenario B)

A lightweight library adds structure to the persistence without the operational overhead of a full platform. Huey gives you retries, scheduling, and SQLite persistence. Transitions gives you formal state machine semantics with escape hatches as transitions.

**Effort:** ~200-300 lines of adapter code. 1 new dependency. Most of hatpin preserved.

**This solves:** Crash recovery, retries, structured state transitions.

**This doesn't solve:** Sub-stage durability, concurrent workflows out of the box.

### Unlikely: "DBOS or Temporal" (Scenario C)

Only justified if hatpin needs **parallel workflow runs** (see §10), rich observability dashboards, and reliable multi-day pause/resume for human-in-the-loop flows. The operational overhead (Postgres for DBOS, Temporal server + Postgres) is significant for a project that currently runs as a single CLI command.

**Effort:** Significant rewrite. New infrastructure. 2-3x the current codebase.

---

## 9. Recommended Next Steps

1. **Start with Phase 0** — implement `save()`/`load()` on `WorkflowContext` + crash recovery. 1-2 hours. This is the cheapest experiment and sets the baseline.
2. **Then Phase 1** — quick screen of Tier **A1** (Huey, transitions, APScheduler). 2–3 hours. Optional: Tier **A2** (Dramatiq) if a Redis/RabbitMQ broker is acceptable.
3. **Only proceed to Tier B/C** if Phase 0 + Tier A both fall short on a specific, documented requirement.

The bar for adopting an external library should be: **does it replace more complexity than it introduces?** Hatpin is ~800 lines of working code. Adding Huey (1 package, SQLite) might be worth it. Adding Temporal (server + database + new programming model) needs a very compelling reason.

---

## 10. Open decisions (product / policy — fill in before heavy evaluation)

These affect how much weight to put on Tier B/C (concurrency, observability platforms) and on persistence design:

1. **Concurrent workflow runs** — **Decision:** Parallel runs may be needed (e.g. multiple issues/repos). Treat **worker pools, queue backends, and isolation between runs** as real evaluation criteria — not only single-process crash recovery. Tier B/C options stay on the table longer than for a strictly serial CLI.
2. **Secrets and persisted artifacts** — **Decision (default team practice):** **Minimize what we persist** (avoid storing raw tool transcripts where possible; keep checkpoints focused on `WorkflowContext` fields needed to resume), treat `**.hatpin/` as machine-local and sensitive** (document in README — do not sync blindly to cloud backup paths), and **defer encryption at rest** until a concrete deployment constraint appears.
  **Tradeoffs accepted:** Faster iteration and simpler code paths vs losing full forensic replay from disk alone; ongoing maintenance of **what** gets serialized as tooling evolves; users who sync project folders must exclude `.hatpin/` or accept exposure risk.
   **Revisit:** [Issue #1 — Revisit persisted-workflow secrets policy](https://github.com/yozlet/hatpin/issues/1).
3. `**human_gate` vs long external waits** — Design choice still open; see subsection below (including **one abstraction, many gate kinds**).

---

### Example: `human_gate` today vs “wait for PR”

Today, `**human_gate`** means: after a stage completes with `PROCEED`, the engine calls `**_human_approval`**, which prints a summary and `**input("Proceed? [y/N]")**` — the **same Python process** blocks until you type `y` or `n`. No timer, no GitHub — it is “stop until the human at this terminal agrees.”

A **deferred PR stage** is different: hatpin would **finish or exit**, someone reviews on GitHub days later, CI runs, then you want hatpin to **resume** (e.g. address review comments). Nothing is waiting at stdin during that time; the trigger is **external** (poll GitHub, webhook, `gh pr view`, etc.).

So the design fork is:

- **Extend `human_gate`** — overload one boolean for both “type y now” and “resume when PR updates” (easy to confuse; different timeouts and UX).
- **Separate mechanism** — keep stdin gates for immediate approval; implement PR stages with persisted state + **poll/webhook/subcommand** (“continue when ready”).
- **Engine-native pause** — Temporal/DBOS-style wait-for-signal; replaces ad hoc polling but adds infrastructure.
- **Single gate type, multiple implementations** — model both as subtypes of one concept, e.g. a `Gate` (or `WorkflowPause`) with pluggable **resolvers**: `StdinGate` (blocks on `input()` in-process) and `ExternalConditionGate` (persists “waiting” and completes when a driver — poll, webhook, or `hatpin resume` — reports the condition). The **engine** always does the same thing: *enter gate → await `gate.until_released()` → continue*. Differences live in the strategy, not in two unrelated features. This avoids duplicating “paused workflow” UX while keeping stdin vs async I/O in separate code paths. **Protocol sketch (names only):** [ADR 0001 — Unified workflow gate protocol](adr/0001-unified-workflow-gate-protocol.md).

Pick based on product UX: a **unified pause model** leans toward the last option; **two user-visible behaviors** (terminal vs days-later GitHub) leans toward **separate mechanism** unless the shared abstraction stays thin.