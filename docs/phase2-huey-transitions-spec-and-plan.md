# Phase 2 (Huey + `transitions`): Evaluation Spec and Plan

**Parent docs:**
- [`docs/research-workflow-engines.md`](./research-workflow-engines.md)
- [`docs/research-workflow-engines-phase0-1-findings.md`](./research-workflow-engines-phase0-1-findings.md)
- Design draft: [`docs/spec-huey-transitions-hybrid.md`](./spec-huey-transitions-hybrid.md)
- Related ADR: [`docs/adr/0001-unified-workflow-gate-protocol.md`](./adr/0001-unified-workflow-gate-protocol.md)

**Date:** 2026-05-05  
**Scope:** Minimal prototype (“tracer bullet”) to validate the Huey+`transitions` hybrid architecture at **stage-level durability**.  
**Non-goal:** Productionize deferred stages 12–14. This is an evaluation/prototype.

**Status (2026-05-06):** Slices A–D and roadmap **Tasks 1–3** are implemented in the spike (`hatpin/workflow_spikes/`). **Tasks 4–5** remain. See [`docs/phase2-huey-transitions-findings.md`](./phase2-huey-transitions-findings.md).

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

See also: [`docs/phase2-huey-transitions-findings.md`](./phase2-huey-transitions-findings.md).

---

## 4. Interfaces (public entrypoints to test)

This is an evaluation spike; the goal is to keep the surface area small and test through a few stable entrypoints.

### 4.1 CLI (optional but preferred)

- `python -m hatpin workflow spike-hybrid run --run-id <id>`: start or continue a run until it reaches a pause or terminal.
- `python -m hatpin workflow spike-hybrid resume --run-id <id>`: release a paused run (either by writing the resume signal or by enqueueing the next tick).

If adding CLI is too invasive for Phase 2, tests may target a module API instead (below) and the CLI can be skipped.

### 4.2 Module API (required)

Define a minimal, testable API (names are intentionally explicit and spike-scoped):

- `hatpin/workflow_spikes/huey_transitions.py`
  - `create_run(run_id: str, *, initial_context: WorkflowContext | None = None) -> None`
  - `run_tick(run_id: str) -> "TickOutcome"`
  - `enqueue_tick(run_id: str) -> None`
  - `resume(run_id: str) -> None`

Public tests should assert:

- correct **checkpoint** contents and progression,
- correct **pause** behavior (no forward progress until resume),
- correct **retry** behavior for transient failures,
- correct **escape hatch** routing (back-edge),
- correct **async stage** execution pattern (no nested event loop hacks).

---

## 5. Spec: Prototype shape (minimum)

### 5.1 Minimal state graph

Use 3–5 states that mirror Hatpin’s real concerns without pulling in GitHub:

- `planning` (async “LLM-like” stage)
- `coding` (mechanical stage)
- `verify` (mechanical stage that can fail and escape back to `coding`)
- `waiting_external` (pause)
- `done` (terminal)

Escape hatch:

- `verify` can transition back to `coding` when a validation condition fails.

### 5.2 Persistence contract (align with Phase 0)

Persist at stage boundaries only, consistent with [`docs/spec-phase0-workflow-persistence.md`](./spec-phase0-workflow-persistence.md):

- Crash during a stage ⇒ rerun that stage on next tick.
- Persist a minimal `WorkflowContext` (two-channel) and state id.
- Omit tool logs by default.

If Phase 2 chooses a DB row instead of JSON, keep the **payload semantics** identical and document the swap in `phase2-huey-transitions-findings.md`.

### 5.3 Gates and pause/resume (align with ADR 0001)

Even if we do not implement the full `WorkflowGate` protocol, the spike should model the same concept:

- stage completes
- optional gate may block further progress
- resume releases the gate and allows next tick

At minimum, represent pause as a persisted checkpoint state with:

- `pause_reason` (e.g. `"external_condition"` or `"human_gate"`)
- `pause_key` (string) identifying what will release it (e.g. `"resume.flag:<run_id>"`)

---

## 6. Plan (vertical slices, TDD)

Timebox: 2–4 hours total.

### Slice A: Graph + checkpoint round-trip (no Huey yet)

- [x] Add a minimal `transitions` graph with 3–5 states in a spike module.
- [x] Define persisted checkpoint: `{run_id, state_id, context, metadata...}` using Phase 0 policy (tool logs off).
- [x] Tests:
  - round-trip `(state_id, context)` → persistence → load → same behavior.
  - persisted payload contains two-channel context (summaries vs facts).

### Slice B: “Tick runner” (in-process)

- [x] Implement `run_tick(run_id)` that:
  - loads checkpoint,
  - runs mechanical stage for current state,
  - applies `transitions` trigger,
  - saves checkpoint,
  - returns whether done.
- [x] Tests:
  - tick advances state and persists after each stage boundary.
  - escape hatch path works (e.g. verification fails → back to coding).

### Slice C: Huey integration (durable queued execution)

- [x] Add Huey optional dependency group for the spike.
- [x] Wrap `run_tick` into a Huey task: `run_workflow_tick(run_id)`.
- [x] Tests:
  - enqueue tick → consume locally → checkpoint updates.
  - prove non-immediate queue → `Worker.loop()` executes the task and advances checkpoint (same process; not a second Huey instance).

### Slice D: Retry and pause/resume semantics

- [x] Add a simulated transient failure mode in one stage (e.g. raise `OSError` once, then succeed).
- [ ] Configure Huey retry behavior for that task (or explicit re-enqueue with backoff) — **deferred to roadmap Task 5** (immediate-mode still uses a manual retry loop).
- [x] Add a pause state (e.g. `waiting_external`) that writes checkpoint and stops enqueueing.
- [x] Resume signal (CLI `resume` or file flag) causes subsequent tick to proceed.
- [x] Tests:
  - transient failure leads to a retry and eventual progress,
  - pause stops progress until resume signal exists.

---

## 7. Acceptance criteria (“Phase 2 succeeded if…”)

- [x] We can encode backward transitions in `transitions` and drive them from stage outcomes.
- [x] We can run an async stage in a Huey task without nested event loop hacks (`run_coroutine_sync`: no `asyncio.run` on a thread that already has a loop; bounded `wait_for` for stage work).
- [x] Checkpointing is correct and minimal (tool logs off by default).
- [x] A crash between stages can be simulated and resume continues at the correct state (stage-boundary semantics; rerun current stage if interrupted mid-stage).
- [x] Retry and pause/resume semantics are demonstrably workable at stage-level granularity (Huey-native retry policy still **Task 5**).

---

## 8. Risks to watch (explicit)

- The hybrid introduces two “next step” mechanisms; the prototype must prove a single source of truth (graph + runner) with Huey as executor only.
- Async execution in Huey may be awkward; if so, we may need:
  - a dedicated async worker runner, or
  - to keep Huey only for mechanical stages and use a different driver for async stages.

---

## 9. Future improvements (ordered task list)

If the spike is promising, do the following in this order (stop early if any step reveals a fundamental mismatch):

- [x] **Task 1 — Prove real Huey execution (not immediate mode)**  
  Done: `test_spike_huey_enqueue_tick_real_worker_advances_checkpoint` enqueues with `immediate=False`, asserts checkpoint still `planning`, runs one `Worker.loop()` (no `consumer.start()`), asserts `planning` → `coding`. See also `get_spike_huey()`.

- [x] **Task 2 — Decide and implement the “blessed” async bridging strategy**  
  Done (spike): `run_coroutine_sync` — `asyncio.run` when no running loop (typical Huey worker thread); helper thread + fresh loop when a loop is already running; `asyncio.wait_for` around stage work with `HATPIN_SPIKE_ASYNC_STAGE_TIMEOUT` (default 300s, `0` = unbounded). Gaps: cooperative cancellation beyond `wait_for`, production-wide timeout policy.

- [x] **Task 3 — Make pause/resume align with ADR 0001 (`WorkflowGate`)**  
  Done (spike): `hatpin/workflow_gate.py`, `hatpin/workflow_spikes/spike_gates.py`, persisted `pause` + `pause_key`, `run_tick` / `resume` through `resolve_gate_for_pause_key`; `checkpoint["pause"]` cleared on successful release; `safe_spike_run_segment` for path safety. Stdin gate exists but is not selectable via persisted `pause_key` in this slice (only `resume.flag:`).

- [ ] **Task 4 — Tighten the persistence contract and schema/versioning**  
  - define canonical `run_id` format
  - define checkpoint location and cleanup policy
  - define fail-closed behavior for `graph_version` mismatch + migration strategy
  - standardize `pause_reason` / `pause_key` in the checkpoint

- [ ] **Task 5 — Clarify and encode retry semantics**  
  Define retryable vs terminal failures; implement Huey-native retries/backoff (instead of spike-only retry loops) and ensure idempotency expectations are explicit.

