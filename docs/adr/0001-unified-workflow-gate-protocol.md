# ADR 0001: Unified workflow gate protocol

**Status:** Proposed  
**Date:** 2026-05-05  

## Context

Hatpin pauses between stages in two different shapes:

1. **Interactive** — same process, stdin (`human_gate` today).
2. **Deferred external** — process may exit; resume when GitHub (or similar) satisfies a condition (future stages 12–14).

We want one orchestrator story—“stage finished, optional gate, then transition”—without inventing unrelated pause mechanisms. Secrets/persistence tradeoffs are tracked in [research-workflow-engines.md §10](../research-workflow-engines.md) and [#1](https://github.com/yozlet/hatpin/issues/1).

## Decision

Introduce a **`WorkflowGate` protocol** (name adjustable at implementation time). Each gate answers **one question**: *given a completed stage result, how does the workflow await permission to continue?* Implementations differ in **how** they wait; the engine does not branch on “stdin vs GitHub” directly—it awaits the gate.

This ADR defines **names and signatures only** (sketch). No runtime code here.

## Protocol sketch

```python
from enum import Enum
from typing import Protocol

from hatpin.context import WorkflowContext
from hatpin.stage import Stage
from hatpin.types import StageResult


class GateOutcome(str, Enum):
    """Whether the workflow may leave the gate."""

    PROCEED = "proceed"  # continue to transition logic
    ABORT = "abort"  # stop workflow (e.g. user declined at stdin)


class WorkflowGate(Protocol):
    """Optional pause after a stage completes with PROCEED (or as configured)."""

    async def until_released(
        self,
        stage: Stage,
        result: StageResult,
        context: WorkflowContext,
    ) -> GateOutcome:
        """Block until the gate condition is satisfied, then return PROCEED or ABORT.

        Callers: workflow engine only. Implementations may:
        - block in-process (stdin),
        - return after persisting a "waiting" marker and complete later via resume,
        - await an external driver (queue, poll loop, durable engine signal).
        """
        ...


# Illustrative implementations (not shipped in this ADR)

class StdinGate:
    """Maps to today's `human_gate` + `_human_approval` behavior."""

    async def until_released(
        self, stage: Stage, result: StageResult, context: WorkflowContext
    ) -> GateOutcome: ...


class ExternalConditionGate:
    """e.g. wait for PR review/CI; may pair with persisted state + poll/webhook/`resume` subcommand."""

    async def until_released(
        self, stage: Stage, result: StageResult, context: WorkflowContext
    ) -> GateOutcome: ...
```

## Stage configuration (sketch)

Replace or generalize the boolean `Stage.human_gate: bool` with something like `Stage.gate: WorkflowGate | None` (or a small factory to avoid pickling async resources). Exact shape is an implementation detail; the ADR only locks the **separation of concerns**: stage definition names *which* gate; `WorkflowGate` handles *how* to wait.

## Consequences

- **Positive:** One place in the engine for “after stage, maybe await gate”; new pause kinds add a class, not a new top-level feature.
- **Negative:** `ExternalConditionGate` must align with durability (what is on disk, what “resume” means). That ties to §10 and issue #1.
- **Migration:** `human_gate=True` → `StdinGate()`; no behavior change until external gates are added.

## Links

- [Research: workflow engines / gates](../research-workflow-engines.md#10-open-decisions-product--policy--fill-in-before-heavy-evaluation)
- [Issue #1 — revisit secrets policy](https://github.com/yozlet/hatpin/issues/1)
