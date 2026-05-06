from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from hatpin.context import WorkflowContext
from hatpin.types import StageOutcome, StageResult


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _state_dir() -> Path:
    override = os.environ.get("HATPIN_SPIKE_STATE_DIR")
    if override:
        return Path(override)
    return Path.cwd() / ".hatpin" / "spikes" / "huey_transitions"


def _checkpoint_path(run_id: str) -> Path:
    # run_id is expected to be a stable identifier; keep filename conservative.
    safe = "".join(c for c in run_id if c.isalnum() or c in ("-", "_", ".", "#"))
    if not safe:
        raise ValueError("run_id produced empty safe filename")
    return _state_dir() / f"{safe}.json"


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


def _resume_flag_path(run_id: str) -> Path:
    return _state_dir() / f"resume.{run_id}.flag"


def resume(run_id: str) -> None:
    """Release a paused run by writing the resume signal."""
    path = _resume_flag_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("resume\n")


def _get_huey():
    from huey import SqliteHuey

    db_path = _state_dir() / "huey.sqlite3"
    key = str(db_path)
    huey = _HUEY_BY_DB.get(key)
    if huey is None:
        huey = SqliteHuey("hatpin-spike-huey-transitions", filename=str(db_path))
        _HUEY_BY_DB[key] = huey
    return huey


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

    # Pause semantics: stop ticking until resume flag exists.
    if prev_state == "waiting_external":
        if not _resume_flag_path(run_id).exists():
            return TickOutcome(
                previous_state_id=prev_state,
                state_id=prev_state,
                is_terminal=False,
                paused=True,
                pause_reason="external_condition",
            )

        # Consume resume signal and proceed.
        _resume_flag_path(run_id).unlink(missing_ok=True)

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

    result: StageResult = asyncio.run(stage_fn(ctx))
    ctx.record_stage(result.stage_name, result.summary, [])

    # Map stage result to state transition.
    if result.outcome == StageOutcome.PROCEED:
        model.proceed()
    elif result.outcome == StageOutcome.NEED_CLARIFICATION and result.escape_target == "coding":
        model.escape_to_coding()
    else:
        # For the spike, treat other non-proceed outcomes as "pause external".
        model.pause_external()
        checkpoint["pause"] = {"reason": result.outcome.value}

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

