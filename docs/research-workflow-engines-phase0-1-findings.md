# Findings: Workflow Engines (Phase 0 + Phase 1)

**Date:** 2026-05-05  
**Parent:** [`docs/research-workflow-engines.md`](./research-workflow-engines.md)  
**Scope:** Summarize what we learned from Phase 0 (DIY persistence) and Phase 1 (Tier A1 screens), based on concrete spike branches and artifacts.  
**Non-goal:** Pick a forever-engine. This is a decision packet to select what to prototype next (Phase 2).

---

## Artifacts produced (branches + spike docs)

All work below was intentionally done on isolated research branches (not merged to `main`).

- **Phase 0 (DIY persistence baseline)**  
  - Branch: `research/phase0-context-persistence`  
  - Spike doc: [`docs/spikes/2026-05-05/spike-phase0-context-persistence.md`](./spikes/2026-05-05/spike-phase0-context-persistence.md)

- **Phase 1 (Tier A1 screens)**
  - Huey  
    - Branch: `research/phase1-huey`  
    - Spike doc: [`docs/spikes/2026-05-05/spike-phase1-huey.md`](./spikes/2026-05-05/spike-phase1-huey.md)
  - APScheduler  
    - Branch: `research/phase1-apscheduler`  
    - Spike doc: [`docs/spikes/2026-05-05/spike-phase1-apscheduler.md`](./spikes/2026-05-05/spike-phase1-apscheduler.md)
  - transitions  
    - Branch: `research/phase1-transitions`  
    - Spike doc: [`docs/spikes/2026-05-05/spike-phase1-transitions.md`](./spikes/2026-05-05/spike-phase1-transitions.md)

---

## What we learned

### 1) Stage-level durability is a viable baseline

Phase 0 confirmed the “just add persistence” hypothesis: persisting **at stage boundaries** (not mid–tool-call) can provide meaningful crash recovery with low conceptual overhead.

- **Implication**: If we accept “re-run the current stage on crash”, we can keep the core Hatpin architecture (stage isolation + deterministic control flow) and avoid adopting a heavy platform early.
- **Still needed**: Strong idempotency/guards for any stage that can cause irreversible side effects, and a clear policy for what is safe to replay.

### 2) The Tier A1 libraries split along two axes

- **Control-flow structure** (graph, escape hatches): `transitions` is a strong fit.
- **Execution durability / retries / concurrency** (queue, worker): Huey is a plausible fit.
- **Scheduling** (time-based jobs): APScheduler is not a workflow engine; it helps only if we explicitly want cron/delay semantics.

### 3) “Retries” live at multiple layers

- **Inside-stage retries** (tool-call retries, backoff, argument repair) are best owned by the agent runtime (e.g. Corvidae), because they depend on tool semantics and prompt strategy.
- **Between-stage retries** (stage task failed; re-run stage) can be owned by the workflow runner (Huey task retries or a DIY loop).
- `transitions` does not provide queue-style retries; it provides exception hooks and a state graph. If we use it, retries are implemented in the runner layer.

---

## Decision: what to do next

### Recommendation

Proceed with a Phase 2 prototype in one of these two shapes:

1. **Phase 0 persistence + `transitions` (structure-first)**  
   Keep execution in-process. Use `transitions` to formally encode stage routing and escape hatches. Persist `(issue_key, next_state, context)` at boundaries.

2. **Phase 0 persistence + Huey + `transitions` (hybrid)**  
   Use `transitions` for the state graph and Huey for durable queued execution + retries + (eventual) multi-run scheduling, while keeping stage semantics and two-channel context in Hatpin.

Given the “partial side effects” concern and the desire for multi-day pause/resume in deferred stages, the **hybrid** is the best candidate to validate next *if* we are willing to run a worker process (even with SQLite).

### Screened out as primary workflow engine

- **APScheduler**: useful as a scheduler, but does not remove the need for a stage machine nor provide step durability at Hatpin’s boundaries.

---

## Key risks to validate in Phase 2

- **Replay safety**: re-running a stage after crash must not create duplicate PRs/comments/branches (or must do so idempotently).
- **State evolution**: persisted `next_stage_index` / state id must remain valid when stage lists change (or we must define migration/invalidations).
- **Hybrid ownership boundaries**: avoid two sources of truth for “what’s next” (Huey pipelines vs Hatpin graph vs runner).
- **Async model**: Huey worker execution is typically not asyncio-first; we need a clean pattern for running async LLM loops inside a task without fragile event loop nesting.
- **Secrets**: persisted state must remain minimal by default (tool logs off), with explicit opt-in and redaction policy if we ever persist transcripts.

