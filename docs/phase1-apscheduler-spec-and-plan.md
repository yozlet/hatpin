# Phase 1 (APScheduler): Evaluation Spec and Plan

**Parent doc:** [research-workflow-engines.md](./research-workflow-engines.md)  
**Date:** 2026-05-05  
**Scope:** Quick technical screen of **APScheduler** only (Tier A1). No implementation in hatpin’s codebase unless explicitly listed as a spike.

---

## 1. Purpose

Decide whether APScheduler is a **viable orchestration layer** for hatpin’s workflow model before investing in a prototype (Phase 2). APScheduler is primarily a **job scheduler** (cron, intervals, date triggers), not a durable workflow engine—Phase 1 answers: *Can we map hatpin stages to scheduled jobs (or a hybrid) without rewriting Corvidae integration, and does persisted job store buy us anything beyond Phase 0’s JSON file?*

---

## 2. Spec: Evaluation Requirements

### 2.1 In scope

| ID | Requirement | Rationale (from research) |
| -- | ----------- | ------------------------- |
| **E1** | Map hatpin concepts to APScheduler primitives | Stages vs `Job` / `JobStore` entries; who owns “what runs next” — scheduler vs existing `WorkflowEngine` |
| **E2** | Assess **escape hatches** (backward jumps) | Research §4.1 — APScheduler has no native DAG or goto; document whether hatpin keeps control flow in-process while APScheduler only fires “run stage N” |
| **E3** | Assess **LLM-in-the-loop**: one unit of work may run **minutes**, many async turns, tool calls | Research §4.1 — verify APScheduler’s expectations for job duration, missed-fire behavior, `max_instances`, coalescing |
| **E4** | Assess **mechanical stages** alongside LLM stages | Same job executor vs separate executors; sync/async job functions |
| **E5** | Assess **two-channel output** (`WorkflowContext.summaries` vs `facts`) | Jobs typically persist metadata in the store—confirm what can be stored vs what must remain in hatpin’s context blob |
| **E6** | Document **persistence and crash recovery**: mid-job vs between jobs | Research §6 — APScheduler “job-level” durability (research Tier A1): typically **between executions**, not mid–tool-call unless the job body is hatpin’s full stage loop |
| **E7** | Estimate **invasiveness**: can `engine.py`, `stage.py`, and tools stay mostly intact? | Research §5 Phase 1 output — likely APScheduler becomes a **thin driver** or **optional** resume timer, not a replacement orchestrator |
| **E8** | Document **asyncio / Corvidae path**: `AsyncIOScheduler` vs blocking scheduler in thread pool; how `LLMClient` and `run_agent_turn` run | Research §4.3 |
| **E9** | Operational footprint: **SQLAlchemy job store** (e.g. SQLite file) for local/single-CLI use vs memory store | Research Tier A1 — avoid requiring Postgres unless evaluation proves necessary |
| **E10** | Note implications for **parallel workflow runs** (decision in research §10) | Multiple schedulers, job ids per issue, store-level isolation; APScheduler is not a distributed queue—document limits |

### 2.2 APScheduler-specific questions (must answer)

| ID | Question |
| -- | -------- |
| **S1** | What problem does APScheduler solve for hatpin that **Phase 0 file persistence** does not? (Retries? Cron-like “retry later”? visibility into scheduled next run?) |
| **S2** | Is the natural mapping **one job per stage invocation** with dynamic `add_job`, or **one long-lived workflow process** with APScheduler only for **deferred wake-ups** (stages 12–14 style)? |
| **S3** | Does using APScheduler **duplicate** scheduling semantics already expressed by `WorkflowEngine`’s loop (risk: two sources of truth)? |

### 2.3 Out of scope for Phase 1

- Production integration, adapter code in `hatpin/`, or dependency changes merged to main (those belong to **Phase 2** unless a ≤30-minute spike is explicitly approved).
- Full comparison matrix for Huey, transitions, Dramatiq (optional follow-up; this document is **APScheduler-only**).
- Encryption at rest, retention policy detail — only cite research §10 and [issue #1](https://github.com/yozlet/hatpin/issues/1) where persistence touches secrets.

### 2.4 Deliverables (artifacts)

1. **APScheduler evaluation memo** (this repo: append to this file under §5 “Findings”, or a short `docs/phase1-apscheduler-findings.md` if findings grow large).
2. **Decision summary** (≤1 page): **Proceed to Phase 2 with APScheduler**, **Defer APScheduler**, or **Need spike** — with explicit reasons tied to E1–E10 and S1–S3.
3. **Sketch** (non-binding): one paragraph + optional pseudo-code for the **least invasive** integration: e.g. “scheduler only wakes `hatpin resume`” vs “each stage is a job.” Include where `WorkflowContext` lives (job `kwargs`, external JSON per Phase 0, SQLAlchemy store fields).

### 2.5 Acceptance criteria

Phase 1 is **complete** when all are true:

- [ ] Every row in §2.1 and §2.2 has a written answer (even “unknown — needs spike”) with doc/API citations.
- [ ] Clear statement on **crash recovery granularity** with APScheduler for hatpin’s chosen mapping (typically: **between job executions** unless the job wraps an entire stage and relies on Phase 0-style context save inside the job).
- [ ] Clear statement on **backward jumps**: APScheduler does not model them — document how hatpin’s engine **remains authoritative** for transitions and what APScheduler stores (if anything).
- [ ] **Complexity delta** called out: does APScheduler reduce glue vs “DIY JSON persistence” from Phase 0, or mostly add scheduler/store concepts for marginal gain?
- [ ] Recommendation aligns with research §8 Scenario A vs B (file persistence vs lightweight library) and explicitly compares to **Huey**-style task queues for the same hatpin mapping.

---

## 3. Plan: Execution Steps

Time budget: **~45–90 minutes** for APScheduler alone (aligned with research §5 Phase 1 “~45 minutes each” for Tier A1 screens).

| Step | Activity | Time | Output |
| ---- | -------- | ---- | ------ |
| **1** | Read APScheduler docs: triggers, job stores, `AsyncIOScheduler` vs `BackgroundScheduler`, **misfire** / **coalesce** / **max_instances** | 20 min | Notes: core nouns (scheduler, job, job store, executor) |
| **2** | Read persistence chapter: SQLAlchemy + SQLite URL, what is serialized (job id, next run time, func ref — **not** arbitrary workflow state by default) | 15 min | Note: what must still live in hatpin’s `WorkflowContext` file |
| **3** | Answer **E2** / **S2** / **S3**: orchestrator-in-hatpin vs scheduler-as-orchestrator; dynamic `add_job` / `reschedule` for “escape” | 15 min | Paragraph: recommended pattern (likely: engine owns graph, scheduler optional) |
| **4** | Answer **E3** / **E6**: long job body; process crash during job; interaction with Phase 0 saves | 15 min | Bullet flow: durability boundaries |
| **5** | Answer **E7** / **E8**: touchpoints with `WorkflowEngine`, `StageRunner`, `WorkflowContext` | 15 min | List of files/classes likely to change |
| **6** | Answer **E9** / **E10** / **S1**: SQLite job store path, multiple issues as separate job ids | 10 min | Ops checklist + “why not just JSON?” |
| **7** | Fill **decision summary** and **acceptance criteria** checklist in §2.5 | 10 min | Go / no-go / spike |

### 3.1 Optional spike (only if blocked)

If docs leave ambiguity on async execution or serializing custom payloads in jobs:

- **Spike:** ≤30 minutes — minimal `apscheduler` + SQLAlchemy SQLite in a throwaway venv, one `AsyncIOScheduler` job that sleepsasync, verify store survives process restart (job still defined vs job runs again). Do **not** commit dependency changes unless the team agrees.

### 3.2 References in this codebase (for step 5)

When estimating invasiveness, explicitly consider:

- `hatpin/engine.py` — `WorkflowEngine.run`, iteration guard, human gates
- `hatpin/stage.py` — `Stage`, `StageRunner`
- `hatpin/context.py` — `WorkflowContext`
- `hatpin/workflows/issue.py` — stage list and deferred 12–14
- [spec-phase0-workflow-persistence.md](./spec-phase0-workflow-persistence.md) — if Phase 0 lands first, APScheduler should be evaluated **together with** that persistence boundary

---

## 4. Risks and biases

| Risk | Mitigation |
| ---- | ---------- |
| Confusing **cron scheduling** with **workflow orchestration** | Name explicitly: “APScheduler fires triggers; hatpin `WorkflowEngine` still decides stage order unless we deliberately defer that.” |
| Over-scoping Phase 1 into a prototype | Stop at deliverables in §2.4; prototype = Phase 2 |
| Assuming APScheduler persists **workflow state** | Default: it persists **jobs** (schedule metadata); hatpin state likely remains Phase 0 JSON unless proven otherwise |
| Choosing APScheduler for **multi-day PR wait** before validating **polling/`resume` CLI** | Document S2: scheduler may only complement external-event drivers |

---

## 5. Findings (to be filled during Phase 1)

*After completing the plan, record concise answers to E1–E10, S1–S3, citations, and the go/no-go here or in `docs/phase1-apscheduler-findings.md`.*

| ID | Answer |
| -- | ------ |
| E1 | |
| E2 | |
| … | |
| S1 | |
| S2 | |
| S3 | |

**Recommendation:** *(Proceed to Phase 2 with APScheduler / Defer / Spike needed)*

**Rationale:**

---

## 6. Traceability

| Research section | Covered by |
| ---------------- | ---------- |
| §5 Phase 1 (Tier A1 screen) | §3 Plan |
| §4 Evaluation criteria | §2.1 E1–E10, §2.2 S1–S3 |
| §6 Durability granularity | E6, §2.5 |
| §3 Tier A1 APScheduler row | §1 Purpose, §4 Risks |
| §8 Expected outcomes | §2.5 acceptance, §4 Risks |
