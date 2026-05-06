"""Tests for ADR 0001 WorkflowGate protocol and spike gate helpers."""

import json

import pytest

from hatpin.context import WorkflowContext
from hatpin.stage import Stage
from hatpin.types import StageOutcome, StageResult
from hatpin.workflow_gate import GateOutcome, GateReleaseNotReady, WorkflowGate


def _minimal_stage(name: str = "coding") -> Stage:
    return Stage(name=name, instruction="spike")


def test_gate_outcome_values_match_adr_sketch():
    assert GateOutcome.PROCEED.value == "proceed"
    assert GateOutcome.ABORT.value == "abort"


@pytest.mark.asyncio
async def test_external_file_gate_proceeds_when_signal_file_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.spike_gates import ExternalFileWorkflowGate

    run_id = "r-gate-1"
    gate = ExternalFileWorkflowGate.for_run(run_id)
    gate.signal_release()

    stage = _minimal_stage()
    result = StageResult(
        stage_name="coding",
        outcome=StageOutcome.BLOCKED,
        summary="waiting",
    )
    ctx = WorkflowContext()

    outcome = await gate.until_released(stage, result, ctx)
    assert outcome is GateOutcome.PROCEED
    assert not gate.resume_file_path.exists()


@pytest.mark.asyncio
async def test_external_file_gate_raises_not_ready_without_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.spike_gates import ExternalFileWorkflowGate

    gate = ExternalFileWorkflowGate.for_run("r-gate-2")
    stage = _minimal_stage()
    result = StageResult(
        stage_name="coding",
        outcome=StageOutcome.BLOCKED,
        summary="waiting",
    )
    ctx = WorkflowContext()

    with pytest.raises(GateReleaseNotReady):
        await gate.until_released(stage, result, ctx)


@pytest.mark.asyncio
async def test_stdin_gate_returns_proceed_on_yes_line(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.spike_gates import StdinWorkflowGate

    gate = StdinWorkflowGate(readline=lambda: "y\n")
    stage = _minimal_stage()
    result = StageResult(stage_name="coding", outcome=StageOutcome.PROCEED, summary="ok")
    ctx = WorkflowContext()

    outcome = await gate.until_released(stage, result, ctx)
    assert outcome is GateOutcome.PROCEED


@pytest.mark.asyncio
async def test_stdin_gate_returns_abort_on_no_line(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.spike_gates import StdinWorkflowGate

    gate = StdinWorkflowGate(readline=lambda: "n\n")
    stage = _minimal_stage()
    result = StageResult(stage_name="coding", outcome=StageOutcome.PROCEED, summary="ok")
    ctx = WorkflowContext()

    outcome = await gate.until_released(stage, result, ctx)
    assert outcome is GateOutcome.ABORT


def test_spike_gates_satisfy_workflow_gate_protocol():
    from hatpin.workflow_spikes.spike_gates import ExternalFileWorkflowGate, StdinWorkflowGate

    ef: WorkflowGate = ExternalFileWorkflowGate.for_run("x")
    sg: WorkflowGate = StdinWorkflowGate(readline=lambda: "y\n")
    assert ef is not None and sg is not None


@pytest.mark.asyncio
async def test_run_tick_waiting_external_uses_gate_not_ad_hoc_path(tmp_path, monkeypatch):
    """Pause/resume flows through WorkflowGate.until_released (file-backed)."""
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    import hatpin.workflow_spikes.spike_gates as spike_gates_mod

    from hatpin.workflow_spikes.huey_transitions import create_run, resume, run_tick

    real_for_run = spike_gates_mod.ExternalFileWorkflowGate.for_run

    calls: list[str] = []

    class InstrumentedGate:
        @classmethod
        def for_run(cls, run_id: str):
            g = real_for_run(run_id)
            orig = g.until_released

            async def wrapped(stage, result, context):
                calls.append("until_released")
                return await orig(stage, result, context)

            g.until_released = wrapped  # type: ignore[method-assign]
            return g

    monkeypatch.setattr(spike_gates_mod, "ExternalFileWorkflowGate", InstrumentedGate)

    ctx = WorkflowContext()
    ctx.facts["_pause_in_coding"] = True
    create_run("r-gate-tick", initial_context=ctx)

    run_tick("r-gate-tick")  # planning -> coding
    run_tick("r-gate-tick")  # coding -> waiting_external (gate not polled until next tick)
    out_paused = run_tick("r-gate-tick")  # waiting_external: gate until_released, file missing
    assert out_paused.state_id == "waiting_external"
    assert calls == ["until_released"]

    resume("r-gate-tick")
    run_tick("r-gate-tick")
    assert calls == ["until_released", "until_released"]


def test_checkpoint_includes_pause_key_when_paused(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run, run_tick
    from hatpin.workflow_spikes.spike_gates import external_file_pause_key

    ctx = WorkflowContext()
    ctx.facts["_pause_in_coding"] = True
    create_run("r-pause-key", initial_context=ctx)

    run_tick("r-pause-key")
    run_tick("r-pause-key")

    payload = json.loads((tmp_path / "r-pause-key.json").read_text())
    assert payload["state_id"] == "waiting_external"
    assert payload.get("pause", {}).get("pause_key") == external_file_pause_key("r-pause-key")


def test_pause_cleared_after_resume_proceed_advances_state(tmp_path, monkeypatch):
    """After PROCEED, checkpoint must not retain pause metadata while state advances."""
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run, resume, run_tick

    ctx = WorkflowContext()
    ctx.facts["_pause_in_coding"] = True
    create_run("r-clear-pause", initial_context=ctx)

    run_tick("r-clear-pause")
    run_tick("r-clear-pause")
    paused_json = json.loads((tmp_path / "r-clear-pause.json").read_text())
    assert paused_json["state_id"] == "waiting_external"
    assert paused_json.get("pause") is not None

    resume("r-clear-pause")
    out = run_tick("r-clear-pause")
    assert out.state_id == "verify"
    assert out.paused is False

    payload = json.loads((tmp_path / "r-clear-pause.json").read_text())
    assert payload["state_id"] == "verify"
    assert payload.get("pause") is None


def test_resume_and_run_tick_share_resolve_gate_patch(tmp_path, monkeypatch):
    """resume() must route through the same resolver as run_tick (single monkeypatch surface)."""
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    import hatpin.workflow_spikes.spike_gates as spike_gates_mod

    from hatpin.workflow_spikes.huey_transitions import create_run, resume, run_tick

    calls: list[str] = []

    real_resolve = spike_gates_mod.resolve_gate_for_pause_key

    def wrapped_resolve(pause_key: str):
        calls.append(pause_key)
        return real_resolve(pause_key)

    monkeypatch.setattr(spike_gates_mod, "resolve_gate_for_pause_key", wrapped_resolve)

    ctx = WorkflowContext()
    ctx.facts["_pause_in_coding"] = True
    create_run("r-shared", initial_context=ctx)

    run_tick("r-shared")
    run_tick("r-shared")
    run_tick("r-shared")  # waiting_external, GateReleaseNotReady

    from hatpin.workflow_spikes.spike_gates import external_file_pause_key

    resume("r-shared")
    assert calls[-1] == external_file_pause_key("r-shared")

    run_tick("r-shared")
    assert external_file_pause_key("r-shared") in calls
    assert calls.count(external_file_pause_key("r-shared")) >= 2


def test_bad_run_id_rejected_no_escape_from_spike_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run

    with pytest.raises(ValueError, match="parent-directory|empty"):
        create_run("../evil")

    assert list(tmp_path.iterdir()) == []


def test_run_tick_rejects_tampered_pause_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run, run_tick

    ctx = WorkflowContext()
    ctx.facts["_pause_in_coding"] = True
    create_run("r-tamper", initial_context=ctx)

    run_tick("r-tamper")
    run_tick("r-tamper")

    bad_path = tmp_path / "r-tamper.json"
    payload = json.loads(bad_path.read_text())
    payload["pause"]["pause_key"] = "resume.flag:../../etc/passwd"
    bad_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="parent-directory|Invalid"):
        run_tick("r-tamper")


def test_waiting_external_gate_abort_marks_done(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    import hatpin.workflow_spikes.spike_gates as spike_gates_mod

    from hatpin.workflow_spikes.huey_transitions import create_run, run_tick

    ctx = WorkflowContext()
    ctx.facts["_pause_in_coding"] = True
    create_run("r-abort", initial_context=ctx)

    run_tick("r-abort")
    run_tick("r-abort")

    class AbortGate:
        async def until_released(self, stage, result, context):
            return GateOutcome.ABORT

    monkeypatch.setattr(spike_gates_mod, "resolve_gate_for_pause_key", lambda _pk: AbortGate())

    out = run_tick("r-abort")
    assert out.state_id == "done"
    assert out.is_terminal is True

    payload = json.loads((tmp_path / "r-abort.json").read_text())
    assert payload["state_id"] == "done"
    assert payload.get("pause") is None


def test_gate_release_not_ready_pause_reason_from_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run, run_tick

    ctx = WorkflowContext()
    ctx.facts["_pause_in_coding"] = True
    create_run("r-reason", initial_context=ctx)

    run_tick("r-reason")
    run_tick("r-reason")

    out = run_tick("r-reason")
    assert out.paused is True
    assert out.pause_reason == "blocked"


def test_external_file_gate_rejects_path_outside_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from pathlib import Path

    from hatpin.workflow_spikes.spike_gates import ExternalFileWorkflowGate

    outside = Path(tmp_path.parent) / "outside.flag"
    with pytest.raises(ValueError, match="escapes spike state"):
        ExternalFileWorkflowGate(outside)
