# Phase 1 (transitions): Evaluation Spec and Plan

**Parent doc:** [research-workflow-engines.md](./research-workflow-engines.md)  
**Date:** 2026-05-05  
**Scope:** Quick technical screen of **`transitions`** ([pytransitions](https://github.com/pytransitions/transitions)) only (Tier A1). No implementation in hatpin’s codebase unless explicitly listed as a spike.

---

## 1. Purpose

Decide whether **`transitions`** is a **viable control-flow layer** for hatpin’s workflow model before investing in a prototype (Phase 2). Phase 1 answers: *Can a declarative state machine express our stages and escape hatches without fighting asyncio or Corvidae — and what does durability look like when persistence is entirely DIY?*

**Why transitions is distinct from Huey/APScheduler:** It models **states and transitions** explicitly (aligned with “orchestrator decides moves; LLM signals outcomes”). It does **not** ship a task queue or durable runner—**execution and persistence are hatpin’s responsibility**.

---

## 2. Spec: Evaluation Requirements

### 2.1 In scope

| ID | Requirement | Rationale (from research) |
| -- | ----------- | ------------------------- |
| **E1** | Map hatpin concepts to `transitions` primitives | Stages vs **states**; moves vs **transitions** / **triggers**; where `WorkflowEngine` loop lives vs `Machine` |
| **E2** | Assess **escape hatches** (backward jumps to earlier stages) | Research §4.1 — transitions’ strength on paper; verify **cycles**, self-transitions, and guard readability |
| **E3** | Assess **LLM-in-the-loop**: one unit of work may run **minutes**, many async turns | Machine callbacks are typically sync; evaluate **`AsyncMachine`** (or running sync machine on executor) vs keeping LLM loop **outside** the machine |
| **E4** | Assess **mechanical stages** alongside LLM stages | `on_enter_*` / `on_exit_*` vs explicit transition callbacks; mixing sync mechanical code with async LLM stages |
| **E5** | Assess **two-channel output** (`WorkflowContext.summaries` vs facts) | State machine does not own facts — confirm it stays on `WorkflowContext` without serializing the whole history into “state” |
| **E6** | Document **persistence and crash recovery** | Research §3 — **no built-in persistence**; define what gets serialized (**current state** + `WorkflowContext` blob vs full Machine); mid-stage vs between transitions |
| **E7** | Estimate **invasiveness**: can `engine.py`, `stage.py`, and tools stay mostly intact? | Research §5 Phase 1 — likely: thin adapter that **maps outcomes → `trigger()`**, or Machine wraps stage list |
| **E8** | Document **asyncio / Corvidae path** | `AsyncMachine` API, event loop ownership, and whether **stage execution** should remain a plain async function **called from** a minimal driver |
| **E9** | Operational footprint | Tier A1: **one package**, no broker; confirm dependency size and optional extras (`diagrams`, etc.) |
| **E10** | **Parallel workflow runs** (research §10) | State machines are **per-instance**; document isolation (one `Machine` per run) vs shared process globals |

### 2.2 Out of scope for Phase 1

- Production integration, adapter code in `hatpin/`, or dependency changes merged to main (**Phase 2** unless a ≤30-minute spike is explicitly approved).
- Full comparison matrix for Huey, APScheduler, Dramatiq (optional follow-up; this document is **transitions-only**).
- Encryption at rest, retention policy detail — cite research §10 and [issue #1](https://github.com/yozlet/hatpin/issues/1) only where persistence touches secrets.

### 2.3 Deliverables (artifacts)

1. **transitions evaluation memo** — append to §5 “Findings” in this file, or `docs/phase1-transitions-findings.md` if it grows large.
2. **Decision summary** (≤1 page): **Proceed to Phase 2 with transitions**, **Defer transitions**, or **Need spike** — with reasons tied to E1–E10.
3. **Sketch** (non-binding): one paragraph + optional pseudo-code showing **one hatpin stage transition**: from state *S₁* to *S₂* via outcome / guard, where `StageRunner` or `run_agent_turn` runs relative to `before`/`after` transition callbacks.

### 2.4 Acceptance criteria

Phase 1 is **complete** when all are true:

- [ ] Every row in §2.1 has a written answer (even “unknown — needs spike”) with doc/API citations (prefer current **pytransitions** readthedocs or GitHub).
- [ ] Clear statement on **crash recovery granularity**: typically **after a transition** (state known) + serialized `WorkflowContext`, **not** mid–tool-call unless hatpin adds extra checkpoints (research §6).
- [ ] Clear statement on **escape hatches**: **first-class** (explicit back-edges), **awkward** (dynamic transition lists), or **poor fit** — with a tiny example state diagram or bullet list of transitions.
- [ ] **Complexity delta**: does `transitions` **reduce** if/then stage routing vs Phase 0 JSON persistence alone, or mostly **reformat** the same orchestration?
- [ ] Recommendation aligns with research §8 Scenario A (DIY persistence) vs B (lightweight library for structure).

---

## 3. Plan: Execution Steps

Time budget: **~45–90 minutes** for transitions alone (research §5 allocates ~45 min per Tier A1 library; allow buffer for AsyncMachine and persistence notes).

| Step | Activity | Time | Output |
| ---- | -------- | ---- | ------ |
| **1** | Read pytransitions docs: `Machine`, `State`, triggers, **transitions** list, **conditions** (`conditions=` / `unless` / `unless`), **prepare_event** / callback order | 20 min | Glossary: trigger, source, dest, internal vs external transitions |
| **2** | Read **graph / cyclic** workflows: multiple edges to same state, self-transitions, reflexive transitions — map to “go back to PLAN” style hops | 15 min | 3–5 bullet patterns for escape hatches |
| **3** | Read **persistence story**: what is serializable (`model.state`, custom state resolution); **no** built-in journal — tie to E6 | 15 min | Bullet: minimal resume tuple `(state_id, workflow_context_payload)` |
| **4** | Read **`AsyncMachine`** (or async extension docs): how callbacks interact with coroutines; whether long-running `await` belongs inside `on_enter` vs outside | 20 min | Recommendation: driver loop shape (1–2 sentences) |
| **5** | Answer **E3** / **E7** / **E8**: sketch where `StageRunner` runs — inside callback vs orchestrator calls `trigger()` then runs stage | 20 min | ASCII or list: `run_issue_workflow` vs `Machine` ownership |
| **6** | Answer **E5** / **E10**: confirm two-channel output stays on `WorkflowContext`; note thread/process safety if ever multi-worker | 10 min | Short paragraph |
| **7** | Fill **decision summary** and §2.4 acceptance checklist; complete §5 Findings table | 15 min | Go / no-go / spike |

### 3.1 Optional spike (only if blocked)

If docs leave ambiguity on **AsyncMachine** behavior with long-running coroutines or nested event loops:

- **Spike:** ≤30 minutes — throwaway venv, `pip install transitions`, minimal `AsyncMachine` with an `asyncio.sleep` “stage” and a trigger chain. Do **not** commit dependency changes unless the team agrees.

### 3.2 References in this codebase (for steps 4–5)

When estimating invasiveness, explicitly consider:

- `hatpin/engine.py` — `WorkflowEngine.run`, iteration guard, human gates
- `hatpin/stage.py` — `Stage`, `StageRunner`
- `hatpin/context.py` — `WorkflowContext`
- `hatpin/workflows/issue.py` — stage list order and deferred stages 12–14

### 3.3 Comparison hook (for Phase 3 / matrix)

When filling research §7 Decision Matrix, ensure **transitions** row can be completed for: durable execution (🟡 + glue), escape hatches, LLM loop, mechanical stages, new packages (1), servers/databases (none), lines preserved (estimate).

---

## 4. Risks and biases

| Risk | Mitigation |
| ---- | ---------- |
| Treating **state** as the entire workflow history | Keep **state = current stage id**; persist **context** separately (E5, E6) |
| Putting entire **async LLM loop** inside `on_enter_*` | Prefer **orchestrator owns await**; Machine encodes **allowed next states** only |
| Over-scoping into Phase 2 prototype | Stop at §2.3 deliverables; spike cap 30 min |
| Confusing **graph visualization extras** with core library | Evaluate **core** `Machine` first; `GraphMachine` optional note only |

---

## 5. Findings (to be filled during Phase 1)

*After completing the plan, record concise answers to E1–E10, citations, and the go/no-go here or in `docs/phase1-transitions-findings.md`.*

| ID | Answer |
| -- | ------ |
| E1 | |
| E2 | |
| E3 | |
| E4 | |
| E5 | |
| E6 | |
| E7 | |
| E8 | |
| E9 | |
| E10 | |

**Recommendation:** *(Proceed to Phase 2 with transitions / Defer / Spike needed)*

**Rationale:**

---

## 6. Traceability

| Research section | Covered by |
| ---------------- | ---------- |
| §3 Tier A1 `transitions` row | §1 Purpose, §2.1 E9 |
| §5 Phase 1 | §3 Plan |
| §4 Evaluation criteria | §2.1 E1–E10 |
| §6 Durability granularity | E6, §2.4 |
| §8 Scenario B (“Huey or transitions”) | §2.4 acceptance, §4 Risks |
