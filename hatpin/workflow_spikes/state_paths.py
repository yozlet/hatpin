"""Shared filesystem locations for the Huey + transitions spike."""

from __future__ import annotations

import os
from pathlib import Path


def safe_spike_run_segment(run_id: str) -> str:
    """Conservative filename segment for checkpoints and gate signal paths.

    Aligns with checkpoint naming in ``huey_transitions``. Rejects empty
    segments and ``..`` substrings (path traversal attempts after stripping
    separators still leave ``..`` in the segment).
    """
    safe = "".join(c for c in run_id if c.isalnum() or c in ("-", "_", ".", "#"))
    if not safe:
        raise ValueError("run_id produced empty safe filename segment")
    if ".." in safe:
        raise ValueError("run_id must not contain parent-directory segments")
    return safe


def spike_state_dir() -> Path:
    override = os.environ.get("HATPIN_SPIKE_STATE_DIR")
    if override:
        return Path(override)
    return Path.cwd() / ".hatpin" / "spikes" / "huey_transitions"
