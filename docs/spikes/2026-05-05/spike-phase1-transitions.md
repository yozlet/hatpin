# Phase 1 spike: `transitions` (pytransitions)

**Branch:** `research/phase1-transitions`  
**Artifacts:** `spike/phase1_transitions.py`, `tests/spike/test_phase1_transitions_spike.py`  
**Install:** `uv sync --extra transitions-spike`

---

## TL;DR

| Topic | Finding |
| ----- | ------- |
| **Escape hatches / backward jumps** | **First-class.** Define extra transitions whose `dest` is an earlier state (cycles allowed). No special API — same as forward edges. |
| **Mechanical (sync) stages** | **`after` / `before` / `on_enter_*`** callbacks on `Machine` — ordinary sync functions. |
| **Long async LLM-style steps** | **`AsyncMachine`** (`transitions.extensions.asyncio`): triggers become coroutines; **`await model.advance()`**. Callbacks can `await`. Fits multi-turn loops *inside* callbacks; alternatively keep awaits in an orchestrator and use the machine only for allowed transitions (recommended in Hatpin spec — both shapes work). |
| **Two-channel output** | **Outside the library.** Keep summaries/facts on a dataclass (`SpikeContext`); the machine only tracks **current state name**. |
| **Persistence / durability** | **No workflow journal.** Options: (1) serialize **`model.state`** + your own JSON for context — resume granularity is **after a successful transition** unless you add checkpoints; (2) **`pickle` the whole `Machine`** — works but ties blobs to code/version; (3) DIY SQLite/JSON for `(state_id, context)`. **Mid–tool-call recovery** is not provided — same as Phase 0 JSON baseline (stage-level unless you add finer checkpoints). |
| **Operational footprint** | **One package** (~112KB class), no broker/DB from the library. |
| **Isolation / parallel runs** | **One `Machine` instance per workflow run** — no globals; safe per-process concurrency if each run owns its model. |

---

## Comparison vs Hatpin’s needs ([research-workflow-engines.md](../../research-workflow-engines.md))

- **Fit for stage routing:** Strong — stages map to **states**, outcomes map to **`trigger(...)`** with optional guards (`conditions=`).
- **Fit for durability:** **Structural only** — gives labeled states and valid transitions; **crash recovery is identical in granularity to “persist `WorkflowContext` + current stage string”** unless you pickle checkpoints inside callbacks.
- **Complexity delta:** Replaces ad hoc `if outcome → next stage` with a **declarative transition table** — helpful if the graph grows; for ~11 stages with escape hatches it may be **mostly reformatting** vs Phase 0 file persistence.
- **Corvidae integration:** **Non-invasive** — adapter translates stage results → triggers; LLM loop stays in Python async code you already have.

---

## References

- [pytransitions/transitions](https://github.com/pytransitions/transitions) — `Machine`, `AsyncMachine`, callbacks, pickling.
- Project spec: [`phase1-transitions-spec-and-plan.md`](../../phase1-transitions-spec-and-plan.md).

