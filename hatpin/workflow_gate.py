"""ADR 0001: unified workflow gate protocol (names and behavior sketch).

Optional pause after a stage completes — one interface for stdin, external
conditions, and other release mechanisms. See ``docs/adr/0001-unified-workflow-gate-protocol.md``.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from hatpin.context import WorkflowContext
from hatpin.stage import Stage
from hatpin.types import StageResult


class GateOutcome(str, Enum):
    """Whether the workflow may leave the gate."""

    PROCEED = "proceed"
    ABORT = "abort"


class GateReleaseNotReady(Exception):
    """Raised by a gate when the release condition is not met yet.

    Tick-based orchestrators (e.g. Huey workers) catch this to end the current
    tick without blocking the worker; a later tick or external ``resume`` will
    poll again. Interactive gates (stdin) typically do not raise this.
    """


class WorkflowGate(Protocol):
    """Optional pause after a stage completes with PROCEED (or as configured)."""

    async def until_released(
        self,
        stage: Stage,
        result: StageResult,
        context: WorkflowContext,
    ) -> GateOutcome:
        """Block until the gate condition is satisfied, then return PROCEED or ABORT.

        Callers: workflow engine only. Implementations may block in-process
        (stdin), persist a waiting marker and complete later via ``resume``, or
        raise :exc:`GateReleaseNotReady` when polled and the condition is not
        yet satisfied.
        """
        ...
