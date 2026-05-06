"""Spike implementations of :class:`hatpin.workflow_gate.WorkflowGate`.

``StdinWorkflowGate`` is not reachable via :func:`resolve_gate_for_pause_key`
in this slice (no persisted ``pause_key`` prefix for stdin); use it from tests
or future wiring (e.g. ``stdin:`` prefix). External file gates use
``resume.flag:<run_id>`` with :func:`safe_spike_run_segment`.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path

from hatpin.context import WorkflowContext
from hatpin.stage import Stage
from hatpin.types import StageResult
from hatpin.workflow_gate import GateOutcome, GateReleaseNotReady, WorkflowGate
from hatpin.workflow_spikes.state_paths import safe_spike_run_segment, spike_state_dir


def external_file_pause_key(run_id: str) -> str:
    """Canonical ``pause_key`` for the default external file gate."""
    return f"resume.flag:{safe_spike_run_segment(run_id)}"


def _ensure_under_spike_dir(path: Path) -> None:
    base = spike_state_dir().resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Path escapes spike state directory: {path}") from exc


class ExternalFileWorkflowGate:
    """Durable external release via a signal file under :func:`spike_state_dir`."""

    def __init__(self, resume_file: Path) -> None:
        _ensure_under_spike_dir(resume_file)
        self._resume_file = resume_file

    @classmethod
    def for_run(cls, run_id: str) -> ExternalFileWorkflowGate:
        safe = safe_spike_run_segment(run_id)
        path = spike_state_dir() / f"resume.{safe}.flag"
        return cls(path)

    @property
    def resume_file_path(self) -> Path:
        return self._resume_file

    def signal_release(self) -> None:
        self._resume_file.parent.mkdir(parents=True, exist_ok=True)
        self._resume_file.write_text("resume\n")

    async def until_released(
        self,
        stage: Stage,
        result: StageResult,
        context: WorkflowContext,
    ) -> GateOutcome:
        if not self._resume_file.exists():
            raise GateReleaseNotReady
        self._resume_file.unlink(missing_ok=True)
        return GateOutcome.PROCEED


class StdinWorkflowGate:
    """Interactive approval mapped to ADR stdin / ``human_gate`` behavior."""

    def __init__(self, readline: Callable[[], str] | None = None) -> None:
        self._readline = readline or sys.stdin.readline

    async def until_released(
        self,
        stage: Stage,
        result: StageResult,
        context: WorkflowContext,
    ) -> GateOutcome:
        line = await asyncio.to_thread(self._readline)
        if line.strip().lower() in ("n", "no"):
            return GateOutcome.ABORT
        return GateOutcome.PROCEED


def resolve_gate_for_pause_key(pause_key: str) -> WorkflowGate:
    """Return a gate for a persisted ``pause_key`` (spike: file-backed external only)."""
    if pause_key.startswith("resume.flag:"):
        raw_run_id = pause_key.partition(":")[2]
        if not raw_run_id:
            raise ValueError(f"Invalid pause_key (empty run_id): {pause_key!r}")
        safe_spike_run_segment(raw_run_id)
        return ExternalFileWorkflowGate.for_run(raw_run_id)
    raise ValueError(f"Unknown pause_key: {pause_key!r}")


def spike_signal_resume(run_id: str) -> None:
    """Signal release using the same ``pause_key`` / resolver path as :func:`run_tick`.

    Writes the external file signal via :func:`resolve_gate_for_pause_key`
    (``resume.flag:…``). Tests that patch ``resolve_gate_for_pause_key`` or
    ``ExternalFileWorkflowGate`` consistently affect both polling and explicit
    resume.

    Uses duck typing (``signal_release``) rather than :func:`isinstance` so
    tests can wrap or substitute :class:`ExternalFileWorkflowGate` without
    breaking :func:`resume`.
    """
    gate = resolve_gate_for_pause_key(external_file_pause_key(run_id))
    release = getattr(gate, "signal_release", None)
    if not callable(release):
        raise TypeError("spike_signal_resume requires a gate with a callable signal_release()")
    release()
