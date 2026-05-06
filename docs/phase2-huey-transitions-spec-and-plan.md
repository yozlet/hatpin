# Phase 2 (Huey + `transitions`): Evaluation Spec and Plan

**Parent docs:**
- [`docs/research-workflow-engines.md`](./research-workflow-engines.md)
- [`docs/research-workflow-engines-phase0-1-findings.md`](./research-workflow-engines-phase0-1-findings.md)
- Design draft: [`docs/spec-huey-transitions-hybrid.md`](./spec-huey-transitions-hybrid.md)

**Date:** 2026-05-05  
**Scope:** Minimal prototype (“tracer bullet”) to validate the Huey+`transitions` hybrid architecture at **stage-level durability**.  
**Non-goal:** Productionize deferred stages 12–14. This is an evaluation/prototype.

---

## 1. Purpose

Validate that we can combine:

- `transitions` as the authoritative stage graph (including backward jumps), and
- Huey as the durable executor (queue + retries),

…without losing Hatpin’s core properties (stage isolation, deterministic control flow, two-channel context) and without introducing fragile asyncio patterns.

---

## 2. Spec: Evaluation Requirements

### 2.1 In scope requirements

| ID | Requirement | Why it matters |
| -- | ----------- | -------------- |
| **P1** | Define a minimal state graph (3–5 states) with an escape hatch back-edge | Proves cycles/backward jumps in the hybrid |
| **P2** | Implement one mechanical stage and one async “LLM-like” stage | Proves mixed stage types and asyncio bridging |
| **P3** | Persist checkpoint at stage boundary and resume correctly after crash | Proves durability semantics |
| **P4** | Demonstrate pause + resume via a simple external signal (CLI or flag file) | Proves multi-day wait pattern at least structurally |
| **P5** | Demonstrate stage-level retry on transient failure | Proves Huey adds value beyond Phase 0 baseline |
| **P6** | Keep `WorkflowContext` two-channel semantics (summaries vs facts) | Preserves Hatpin’s architecture and prompts |
| **P7** | Keep invasiveness low (do not rewrite the full workflow engine) | We want an evaluative spike, not a migration |

### 2.2 Out of scope

- Tool-call-level replay/checkpointing.
- Distributed deployment hardening (multi-host workers, secrets vault, encryption-at-rest).
- Full GitHub integration; use local “fake” stages unless strictly needed.

---

## 3. Deliverables

1. A small prototype implementation behind a clearly-named module/flag (spike-quality code is fine, but must be tested).
2. Tests covering P1–P6 through public entrypoints.
3. Short write-up: `docs/phase2-huey-transitions-findings.md` with:
   - what worked,
   - what broke,
   - recommendation: proceed / abandon / adjust design.

---

## 4. Plan (vertical slices, TDD)

Timebox: 2–4 hours total.

### Slice A: Graph + checkpoint round-trip (no Huey yet)

- [ ] Add a minimal `transitions` graph with 3–5 states in a spike module.
- [ ] Define persisted checkpoint: `{run_id, state_id, context}` using Phase 0 JSON policy (tool logs off).
- [ ] Tests:
  - round-trip `(state_id, context)` → file → load → same behavior.

### Slice B: “Tick runner” (in-process)

- [ ] Implement `run_tick(run_id)` that:
  - loads checkpoint,
  - runs mechanical stage for current state,
  - applies `transitions` trigger,
  - saves checkpoint,
  - returns whether done.
- [ ] Tests:
  - tick advances state and persists after each stage boundary.
  - escape hatch path works (e.g. verification fails → back to coding).

### Slice C: Huey integration (durable queued execution)

- [ ] Add Huey optional dependency group for the spike.
- [ ] Wrap `run_tick` into a Huey task: `run_workflow_tick(run_id)`.
- [ ] Tests:
  - enqueue tick → consume locally → checkpoint updates.
  - simulate “new worker instance” (new Huey object) draining same SQLite queue.

### Slice D: Retry and pause/resume semantics

- [ ] Add a simulated transient failure mode in one stage (e.g. raise `OSError` once, then succeed).
- [ ] Configure Huey retry behavior for that task (or explicit re-enqueue with backoff).
- [ ] Add a pause state (e.g. `waiting_external`) that writes checkpoint and stops enqueueing.
- [ ] Resume signal (CLI `resume` or file flag) causes subsequent tick to proceed.
- [ ] Tests:
  - transient failure leads to a retry and eventual progress,
  - pause stops progress until resume signal exists.

---

## 5. Acceptance criteria (“Phase 2 succeeded if…”)

- [ ] We can encode backward transitions in `transitions` and drive them from stage outcomes.
- [ ] We can run an async stage in a Huey task without nested event loop hacks.
- [ ] Checkpointing is correct and minimal (tool logs off by default).
- [ ] A crash between stages can be simulated and resume continues at the correct state.
- [ ] Retry and pause/resume semantics are demonstrably workable at stage-level granularity.

---

## 6. Risks to watch (explicit)

- The hybrid introduces two “next step” mechanisms; the prototype must prove a single source of truth (graph + runner) with Huey as executor only.
- Async execution in Huey may be awkward; if so, we may need:
  - a dedicated async worker runner, or
  - to keep Huey only for mechanical stages and use a different driver for async stages.

