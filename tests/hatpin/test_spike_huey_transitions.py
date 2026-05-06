import json

from hatpin.context import WorkflowContext


def test_spike_create_run_and_tick_persists_context_and_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run, run_tick

    ctx = WorkflowContext()
    ctx.summaries["seed"] = "hello"
    ctx.facts["issue_key"] = "o/r#1"
    create_run("o-r#1", initial_context=ctx)

    out1 = run_tick("o-r#1")
    assert out1.previous_state_id == "planning"
    assert out1.state_id == "coding"

    # Verify persisted checkpoint is minimal and includes two-channel context.
    checkpoint_path = tmp_path / "o-r#1.json"
    payload = json.loads(checkpoint_path.read_text())

    assert payload["state_id"] == "coding"
    assert payload["context"]["summaries"]["seed"] == "hello"
    assert payload["context"]["summaries"]["planning"] == "planned"
    assert payload["context"]["facts"]["issue_key"] == "o/r#1"
    # tool logs are intentionally not persisted in the spike payload
    assert "tool_logs" not in payload["context"]


def test_spike_escape_hatch_round_trip(tmp_path, monkeypatch):
    """Verify can fail verification, escape back, and persist across ticks."""
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run, run_tick

    create_run("r1")

    out1 = run_tick("r1")
    assert out1.state_id == "coding"

    out2 = run_tick("r1")
    assert out2.state_id == "verify"

    # First verify fails and escapes back to coding.
    out3 = run_tick("r1")
    assert out3.previous_state_id == "verify"
    assert out3.state_id == "coding"

    # Next passes through verify to done.
    out4 = run_tick("r1")
    assert out4.state_id == "verify"

    out5 = run_tick("r1")
    assert out5.state_id == "done"
    assert out5.is_terminal is True


def test_spike_pause_and_resume_signal(tmp_path, monkeypatch):
    """Run reaches pause state and blocks until resumed."""
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run, run_tick, resume

    ctx = WorkflowContext()
    ctx.facts["_pause_in_coding"] = True
    create_run("r2", initial_context=ctx)

    run_tick("r2")  # planning -> coding

    out_paused = run_tick("r2")  # coding -> waiting_external (pause)
    assert out_paused.state_id == "waiting_external"
    assert out_paused.paused is True

    resume("r2")
    out_resumed = run_tick("r2")
    assert out_resumed.paused is False


def test_spike_huey_enqueue_tick_immediate_mode(tmp_path, monkeypatch):
    """Huey enqueue should execute a tick when immediate=True (test mode)."""
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run, enqueue_tick, get_spike_huey

    create_run("r3")

    huey = get_spike_huey()
    huey.immediate = True

    enqueue_tick("r3")

    payload = json.loads((tmp_path / "r3.json").read_text())
    assert payload["state_id"] == "coding"


def test_spike_huey_retryable_failure_retries_once_in_immediate_mode(tmp_path, monkeypatch):
    """A retryable transient failure should be retried once in immediate mode."""
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import create_run, enqueue_tick, get_spike_huey
    from hatpin.context import WorkflowContext

    ctx = WorkflowContext()
    ctx.facts["_fail_once_in_coding"] = True
    create_run("r4", initial_context=ctx)

    huey = get_spike_huey()
    huey.immediate = True

    # First tick (planning->coding) executes; second attempt should fail once
    # and then succeed on retry, ending in verify.
    enqueue_tick("r4")  # planning->coding
    enqueue_tick("r4")  # coding stage runs; fail once then retry -> verify

    payload = json.loads((tmp_path / "r4.json").read_text())
    assert payload["state_id"] == "verify"


def test_spike_huey_enqueue_tick_real_worker_advances_checkpoint(tmp_path, monkeypatch):
    """Non-immediate mode: task is queued and Worker.loop executes run_tick once."""
    monkeypatch.setenv("HATPIN_SPIKE_STATE_DIR", str(tmp_path))

    from hatpin.workflow_spikes.huey_transitions import (
        create_run,
        enqueue_tick,
        get_spike_huey,
    )

    run_id = "t1-real-queue"
    create_run(run_id)

    huey = get_spike_huey()
    huey.immediate = False

    enqueue_tick(run_id)

    checkpoint_path = tmp_path / "t1-real-queue.json"
    payload_after_enqueue = json.loads(checkpoint_path.read_text())
    assert payload_after_enqueue["state_id"] == "planning"

    consumer = huey.create_consumer(workers=1, periodic=False)
    worker = consumer.worker_threads[0][0]
    worker.initialize()
    worker.loop()

    payload = json.loads(checkpoint_path.read_text())
    assert payload["state_id"] == "coding"
    assert payload["context"]["summaries"]["planning"] == "planned"
    assert "tool_logs" not in payload["context"]

