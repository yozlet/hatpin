# Phase 1 (Huey): Evaluation Spec and Plan

**Parent doc:** [research-workflow-engines.md](./research-workflow-engines.md)  
**Date:** 2026-05-05  
**Scope:** Quick technical screen of **Huey** only (Tier A1). No implementation in hatpin’s codebase unless explicitly listed as a spike.

---

## 1. Purpose

Decide whether Huey is a **viable orchestration layer** for hatpin’s workflow model before investing in a prototype (Phase 2). Phase 1 answers: *Can Huey express our stages, escape hatches, and long LLM loops without forcing a rewrite of Corvidae integration — and what durability does it actually provide?*

---

## 2. Spec: Evaluation Requirements

### 2.1 In scope

| ID | Requirement | Rationale (from research) |
| -- | ----------- | ------------------------- |
| **E1** | Map hatpin concepts to Huey primitives | Stages vs tasks/pipelines; `WorkflowEngine` vs consumer/worker |
| **E2** | Assess **escape hatches** (backward jumps) | Research §4.1 — most engines assume forward-only DAGs |
| **E3** | Assess **LLM-in-the-loop**: one unit of work may run **minutes**, many async turns, tool calls | Research §4.1 — Huey “step” must not assume sub-second tasks |
| **E4** | Assess **mechanical stages** (sync or short async) alongside LLM stages | Research §4.1 |
| **E5** | Assess **two-channel output** (`WorkflowContext.summaries` vs `facts` / orchestrator facts) | Must remain representable without collapsing into one blob |
| **E6** | Document **persistence and crash recovery**: mid-stage vs between tasks | Research §6 — stage-level vs tool-call-level |
| **E7** | Estimate **invasiveness**: can `engine.py`, `stage.py`, and tools stay mostly intact? | Research §5 Phase 1 output |
| **E8** | Document **asyncio / Corvidae path**: threads vs processes vs async workers; how `LLMClient` and `run_agent_turn` would be invoked | Research §4.3 |
| **E9** | Operational footprint: **SQLite** backend (no Redis) for local/single-CLI use | Research Tier A1 |
| **E10** | Note implications for **parallel workflow runs** (decision in research §10) | Queues/workers may help; document what Huey gives vs hatpin still must design |

### 2.2 Out of scope for Phase 1

- Production integration, adapter code in `hatpin/`, or dependency changes merged to main (those belong to **Phase 2** unless a ≤30-minute spike is explicitly approved).
- Full comparison matrix for APScheduler, transitions, Dramatiq (optional follow-up; this document is **Huey-only**).
- Encryption at rest, retention policy detail — only cite research §10 and [issue #1](https://github.com/yozlet/hatpin/issues/1) where persistence touches secrets.

### 2.3 Deliverables (artifacts)

1. **Huey evaluation memo** (this repo: append to this file under §5 “Findings”, or a short `docs/phase1-huey-findings.md` if findings grow large).
2. **Decision summary** (≤1 page): **Proceed to Phase 2 with Huey**, **Defer Huey**, or **Need spike** — with explicit reasons tied to E1–E10.
3. **Sketch** (non-binding): one paragraph + optional pseudo-code on how a **single hatpin stage** would become a Huey task or pipeline step, including where `WorkflowContext` would live (task argument, serialized blob, Redis/SQLite metadata only).

### 2.4 Acceptance criteria

Phase 1 is **complete** when all are true:

- [ ] Every row in §2.1 has a written answer (even “unknown — needs spike”) with doc/API citations.
- [ ] Clear statement on **crash recovery granularity** with Huey for hatpin’s mapping (typically: **between Huey tasks**, not mid–tool-call unless modeled explicitly).
- [ ] Clear statement on **backward jumps**: supported natively, emulated (dynamic task enqueue), or **poor fit**.
- [ ] **Complexity delta** called out: does Huey reduce glue vs “DIY JSON persistence” from Phase 0, or mostly add worker/process concepts?
- [ ] Recommendation aligns with research §8 Scenario A vs B (file persistence vs lightweight library).

---

## 3. Plan: Execution Steps

Time budget: **2–3 hours** total (aligned with research §5 Phase 1 for one library, expanded slightly for Huey depth).

| Step | Activity | Time | Output |
| ---- | -------- | ---- | ------ |
| **1** | Read Huey docs: overview, consumers, storage backends (SQLite), pipelines/chains if applicable, retries | 30 min | Notes: core nouns (task, queue, consumer, scheduler) |
| **2** | Read Huey API for **async** / blocking tasks — how long-running work is intended to run | 20 min | Note: worker model (thread/process), asyncio support if any |
| **3** | Answer **E2** (escape hatches): dynamic enqueue, revoking tasks, or orchestrator outside Huey | 25 min | Paragraph + pattern |
| **4** | Answer **E3** / **E6**: one “stage” = one task vs pipeline of tasks; idempotency and retries | 25 min | Diagram or bullet flow |
| **5** | Answer **E7** / **E8**: touchpoints with `WorkflowEngine`, `StageRunner`, `WorkflowContext` | 25 min | List of files/classes likely to change |
| **6** | Answer **E9** / **E10**: SQLite path, single worker vs pool, isolation per issue/repo | 15 min | Ops checklist |
| **7** | Fill **decision summary** and **acceptance criteria** checklist in §2.4 | 15 min | Go / no-go / spike |

### 3.1 Optional spike (only if blocked)

If docs leave ambiguity on asyncio or task length limits:

- **Spike:** ≤30 minutes — minimal `huey` + SQLite install in a throwaway venv, one dummy long-running task, verify worker behavior. Do **not** commit dependency changes unless the team agrees.

### 3.2 References in this codebase (for step 5)

When estimating invasiveness, explicitly consider:

- `hatpin/engine.py` — `WorkflowEngine.run`, iteration guard, human gates
- `hatpin/stage.py` — `Stage`, `StageRunner`
- `hatpin/context.py` — `WorkflowContext`
- `hatpin/workflows/issue.py` — stage list and deferred 12–14

---

## 4. Risks and biases

| Risk | Mitigation |
| ---- | ---------- |
| Confusing Huey’s **task** with hatpin’s **stage** | Always name them “Huey task” vs “hatpin stage” in notes |
| Over-scoping Phase 1 into a prototype | Stop at deliverables in §2.3; prototype = Phase 2 |
| Assuming Redis for Huey | Default evaluation path is **SQLite** per research Tier A1 |

---

## 5. Findings (to be filled during Phase 1)

*After completing the plan, record concise answers to E1–E10, citations, and the go/no-go here or in `docs/phase1-huey-findings.md`.*

| ID | Answer |
| -- | ------ |
| E1 | |
| E2 | |
| … | |

**Recommendation:** *(Proceed to Phase 2 with Huey / Defer / Spike needed)*

**Rationale:**

---

## 6. Traceability

| Research section | Covered by |
| ---------------- | ---------- |
| §5 Phase 1 | §3 Plan |
| §4 Evaluation criteria | §2.1 E1–E10 |
| §6 Durability granularity | E6, §2.4 |
| §8 Expected outcomes | §2.4 acceptance, §4 Risks |
