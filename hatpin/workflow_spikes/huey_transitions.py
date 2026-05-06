from __future__ import annotations

import asyncio
import json
import math
import os
import threading
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypeVar

from hatpin.context import WorkflowContext
from hatpin.stage import Stage
from hatpin.types import StageOutcome, StageResult
from hatpin.workflow_gate import GateOutcome, GateReleaseNotReady
from hatpin.workflow_spikes import spike_gates as _spike_gates
from hatpin.workflow_spikes.state_paths import safe_spike_run_segment, spike_state_dir

_T = TypeVar("_T")

# Default cap for a single async stage inside :func:`run_coroutine_sync` (LLM-like work).
SPIKE_ASYNC_STAGE_TIMEOUT_DEFAULT_S = 300.0

FORMAT_VERSION = 1
GRAPH_VERSION = 1


StateId = Literal["planning", "coding", "verify", "waiting_external", "done"]


@dataclass(frozen=True)
class TickOutcome:
    previous_state_id: StateId
    state_id: StateId
    is_terminal: bool
    paused: bool
    pause_reason: str | None = None


def spike_async_stage_timeout_seconds() -> float | None:
    """Seconds for :func:`asyncio.wait_for` around each stage coroutine.

    Reads ``HATPIN_SPIKE_ASYNC_STAGE_TIMEOUT`` (seconds, float). Unset or empty
    string → :data:`SPIKE_ASYNC_STAGE_TIMEOUT_DEFAULT_S` (300). ``0`` disables
    the cap (unbounded; **test / interactive only** — a stuck stage can block a
    Huey worker thread forever).

    Raises:
        ValueError: if the env value is negative or not a finite float.
    """
    raw = os.environ.get("HATPIN_SPIKE_ASYNC_STAGE_TIMEOUT")
    if raw is None or raw.strip() == "":
        return SPIKE_ASYNC_STAGE_TIMEOUT_DEFAULT_S
    limit = float(raw)
    if limit < 0 or not math.isfinite(limit):
        raise ValueError("HATPIN_SPIKE_ASYNC_STAGE_TIMEOUT must be a non-negative finite float")
    if limit == 0:
        return None
    return limit


async def _await_with_stage_timeout(coro: Coroutine[Any, Any, _T], limit: float | None) -> _T:
    if limit is None:
        return await coro
    return await asyncio.wait_for(coro, timeout=limit)


def _run_coroutine_in_fresh_loop(coro: Coroutine[Any, Any, _T], limit: float | None) -> _T:
    async def _go() -> _T:
        return await _await_with_stage_timeout(coro, limit)

    return asyncio.run(_go())


def run_coroutine_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run *coro* to completion from synchronous code (e.g. ``run_tick``, Huey tasks).

    Stage work is bounded with :func:`asyncio.wait_for` when a positive timeout
    is configured (see :func:`spike_async_stage_timeout_seconds`). On expiry,
    raises :exc:`asyncio.TimeoutError` (same as :func:`asyncio.wait_for`).

    Uses a fresh event loop via :func:`asyncio.run` when this thread has no
    running loop — the usual Huey worker thread case. If a loop is already
    running (pytest-asyncio, Jupyter, async callers into ``run_tick``), the
    coroutine runs on a short-lived helper thread with its own loop so we never
    nest :func:`asyncio.run` in the same thread as an active loop.

    Exceptions raised inside the helper thread are re-raised in the caller;
    tracebacks refer to the thread where the failure occurred (acceptable for
    this spike; see findings doc).
    """
    limit = spike_async_stage_timeout_seconds()

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _run_coroutine_in_fresh_loop(coro, limit)

    result: list[_T | None] = [None]
    error: list[BaseException | None] = [None]

    def _runner() -> None:
        try:
            result[0] = _run_coroutine_in_fresh_loop(coro, limit)
        except BaseException as exc:
            error[0] = exc

    thread = threading.Thread(target=_runner, name="hatpin-spike-async-bridge", daemon=True)
    thread.start()
    if limit is None:
        thread.join()
    else:
        # ``wait_for`` bounds the coroutine; join is a backstop only.
        thread.join(timeout=limit + 15.0)
        if thread.is_alive():
            raise RuntimeError(
                "async bridge helper thread did not finish after the stage timeout window; "
                "this is distinct from asyncio.TimeoutError and indicates an internal bug."
            )

    if error[0] is not None:
        raise error[0]
    return result[0]  # type: ignore[return-value]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _checkpoint_path(run_id: str) -> Path:
    safe = safe_spike_run_segment(run_id)
    return spike_state_dir() / f"{safe}.json"


def _serialize_context(ctx: WorkflowContext) -> dict[str, Any]:
    payload = {"summaries": dict(ctx.summaries), "facts": dict(ctx.facts)}
    # Enforce JSON-compatibility of facts (Phase 0 contract).
    json.dumps(payload["facts"])
    return payload


def _deserialize_context(payload: dict[str, Any]) -> WorkflowContext:
    ctx = WorkflowContext()
    ctx.summaries = dict(payload.get("summaries", {}))
    ctx.facts = dict(payload.get("facts", {}))
    # tool_logs are intentionally omitted for persisted state (Phase 0 policy).
    return ctx


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def create_run(run_id: str, *, initial_context: WorkflowContext | None = None) -> None:
    """Create a new spike run, overwriting any existing checkpoint."""
    ctx = initial_context or WorkflowContext()
    checkpoint = {
        "format_version": FORMAT_VERSION,
        "graph_version": GRAPH_VERSION,
        "run_id": run_id,
        "state_id": "planning",
        "updated_at": _now_iso(),
        "context": _serialize_context(ctx),
        "pause": None,
    }
    _atomic_write_json(_checkpoint_path(run_id), checkpoint)


def _load_checkpoint(run_id: str) -> dict[str, Any]:
    path = _checkpoint_path(run_id)
    return json.loads(path.read_text())


def _save_checkpoint(run_id: str, checkpoint: dict[str, Any]) -> None:
    checkpoint["updated_at"] = _now_iso()
    _atomic_write_json(_checkpoint_path(run_id), checkpoint)


class _RunModel:
    def __init__(self, state: StateId) -> None:
        self.state: StateId = state


def _build_machine(model: _RunModel):
    # Import inside to keep spike dependency surface localized.
    from transitions import Machine

    states: list[StateId] = [
        "planning",
        "coding",
        "verify",
        "waiting_external",
        "done",
    ]
    transitions = [
        {"trigger": "proceed", "source": "planning", "dest": "coding"},
        {"trigger": "proceed", "source": "coding", "dest": "verify"},
        {"trigger": "proceed", "source": "verify", "dest": "done"},
        {"trigger": "escape_to_coding", "source": "verify", "dest": "coding"},
        {"trigger": "pause_external", "source": "*", "dest": "waiting_external"},
        {"trigger": "resume", "source": "waiting_external", "dest": "verify"},
    ]
    return Machine(
        model=model,
        states=states,
        transitions=transitions,
        initial=model.state,
        auto_transitions=False,
        ignore_invalid_triggers=False,
    )


async def _stage_planning(ctx: WorkflowContext) -> StageResult:
    await asyncio.sleep(0)
    return StageResult(
        stage_name="planning",
        outcome=StageOutcome.PROCEED,
        summary="planned",
    )


async def _stage_coding(ctx: WorkflowContext) -> StageResult:
    if ctx.facts.get("_pause_in_coding", False):
        return StageResult(
            stage_name="coding",
            outcome=StageOutcome.BLOCKED,
            summary="paused for external condition",
        )
    return StageResult(
        stage_name="coding",
        outcome=StageOutcome.PROCEED,
        summary="coded",
    )


async def _stage_verify(ctx: WorkflowContext) -> StageResult:
    # First time, simulate a verification failure requiring a back-edge.
    if not ctx.facts.get("_verify_failed_once", False):
        ctx.facts["_verify_failed_once"] = True
        return StageResult(
            stage_name="verify",
            outcome=StageOutcome.NEED_CLARIFICATION,
            summary="verification failed; needs more work",
            escape_target="coding",
        )
    return StageResult(
        stage_name="verify",
        outcome=StageOutcome.PROCEED,
        summary="verified",
    )


_STAGES: dict[StateId, Any] = {
    "planning": _stage_planning,
    "coding": _stage_coding,
    "verify": _stage_verify,
}


def resume(run_id: str) -> None:
    """Release a paused run via the same gate resolution as :func:`run_tick` (ADR 0001 spike)."""
    _spike_gates.spike_signal_resume(run_id)


def _get_huey():
    from huey import SqliteHuey

    db_path = spike_state_dir() / "huey.sqlite3"
    key = str(db_path)
    huey = _HUEY_BY_DB.get(key)
    if huey is None:
        huey = SqliteHuey("hatpin-spike-huey-transitions", filename=str(db_path))
        _HUEY_BY_DB[key] = huey
    return huey


def get_spike_huey():
    """Return the SqliteHuey instance used by :func:`enqueue_tick` for this spike.

    Spike/evaluation only — not a stable Hatpin workflow API. Delegates to the
    same singleton keyed by ``HATPIN_SPIKE_STATE_DIR`` / ``huey.sqlite3`` as
    internal enqueue routing.
    """
    return _get_huey()


_HUEY_BY_DB: dict[str, Any] = {}


def enqueue_tick(run_id: str) -> None:
    """Enqueue a tick via Huey (SQLite)."""
    huey = _get_huey()
    run_workflow_tick = _get_tick_task(huey)

    # In tests we set huey.immediate=True to avoid running a worker.
    # In that mode, Huey logs exceptions rather than propagating them in a
    # reliable way, so we execute the tick directly.
    if getattr(huey, "immediate", False):
        attempts = 0
        while True:
            attempts += 1
            try:
                run_tick(run_id)
                return
            except OSError:
                if attempts < 2:
                    continue
                raise

    run_workflow_tick(run_id)


_TICK_TASK_BY_HUEY: dict[int, Any] = {}


def _get_tick_task(huey):
    existing = _TICK_TASK_BY_HUEY.get(id(huey))
    if existing is not None:
        return existing

    @huey.task(name="hatpin_spike_huey_transitions_run_workflow_tick")
    def run_workflow_tick(rid: str) -> None:
        run_tick(rid)

    _TICK_TASK_BY_HUEY[id(huey)] = run_workflow_tick
    return run_workflow_tick


def run_tick(run_id: str) -> TickOutcome:
    checkpoint = _load_checkpoint(run_id)
    if checkpoint.get("format_version") != FORMAT_VERSION:
        raise ValueError("Unsupported checkpoint format_version")
    if checkpoint.get("graph_version") != GRAPH_VERSION:
        raise ValueError("Unsupported checkpoint graph_version")

    ctx = _deserialize_context(checkpoint.get("context", {}))
    state_id = checkpoint.get("state_id")
    if state_id not in ("planning", "coding", "verify", "waiting_external", "done"):
        raise ValueError(f"Unknown state_id: {state_id!r}")

    prev_state: StateId = state_id

    # Simulated retryable failure (stage-level), persisted to avoid failing on
    # every retry attempt.
    if prev_state == "coding" and ctx.facts.get("_fail_once_in_coding", False):
        spike = checkpoint.setdefault("spike", {})
        if not spike.get("failed_once_in_coding", False):
            spike["failed_once_in_coding"] = True
            _save_checkpoint(run_id, checkpoint)
            raise OSError("simulated transient failure")

    if prev_state == "waiting_external":
        pause_meta = checkpoint.get("pause") or {}
        pause_key = pause_meta.get("pause_key") or _spike_gates.external_file_pause_key(run_id)
        gate = _spike_gates.resolve_gate_for_pause_key(pause_key)
        stage_name = pause_meta.get("stage_name", "waiting_external")
        pause_stage = Stage(name=stage_name, instruction="spike")
        try:
            outcome_enum = StageOutcome(pause_meta.get("reason", StageOutcome.BLOCKED.value))
        except ValueError:
            outcome_enum = StageOutcome.BLOCKED
        pause_result = StageResult(
            stage_name=stage_name,
            outcome=outcome_enum,
            summary=pause_meta.get("summary", ""),
        )
        try:
            gate_out = run_coroutine_sync(gate.until_released(pause_stage, pause_result, ctx))
        except GateReleaseNotReady:
            return TickOutcome(
                previous_state_id=prev_state,
                state_id=prev_state,
                is_terminal=False,
                paused=True,
                pause_reason=pause_meta.get("reason") or "external_condition",
            )
        if gate_out == GateOutcome.ABORT:
            checkpoint["state_id"] = "done"
            checkpoint["pause"] = None
            checkpoint["context"] = _serialize_context(ctx)
            _save_checkpoint(run_id, checkpoint)
            return TickOutcome(
                previous_state_id=prev_state,
                state_id="done",
                is_terminal=True,
                paused=False,
            )
        # PROCEED: leave gate — clear pause metadata before advancing state (P1).
        checkpoint["pause"] = None

    if prev_state == "done":
        return TickOutcome(
            previous_state_id=prev_state,
            state_id=prev_state,
            is_terminal=True,
            paused=False,
        )

    model = _RunModel(prev_state)
    machine = _build_machine(model)

    if model.state == "waiting_external":
        model.resume()
        checkpoint["state_id"] = model.state
        checkpoint["context"] = _serialize_context(ctx)
        _save_checkpoint(run_id, checkpoint)
        return TickOutcome(
            previous_state_id=prev_state,
            state_id=model.state,
            is_terminal=False,
            paused=False,
        )

    stage_fn = _STAGES.get(model.state)
    if stage_fn is None:
        raise ValueError(f"No stage implementation for state {model.state!r}")

    result: StageResult = run_coroutine_sync(stage_fn(ctx))
    ctx.record_stage(result.stage_name, result.summary, [])

    # Map stage result to state transition.
    if result.outcome == StageOutcome.PROCEED:
        model.proceed()
    elif result.outcome == StageOutcome.NEED_CLARIFICATION and result.escape_target == "coding":
        model.escape_to_coding()
    else:
        # For the spike, treat other non-proceed outcomes as "pause external".
        model.pause_external()
        checkpoint["pause"] = {
            "reason": result.outcome.value,
            "pause_key": _spike_gates.external_file_pause_key(run_id),
            "stage_name": result.stage_name,
            "summary": result.summary,
        }

    checkpoint["state_id"] = model.state
    checkpoint["context"] = _serialize_context(ctx)
    _save_checkpoint(run_id, checkpoint)

    return TickOutcome(
        previous_state_id=prev_state,
        state_id=model.state,
        is_terminal=(model.state == "done"),
        paused=(model.state == "waiting_external"),
        pause_reason=checkpoint.get("pause", {}).get("reason") if model.state == "waiting_external" else None,
    )

