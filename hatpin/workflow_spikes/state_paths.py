"""Shared filesystem locations for the Huey + transitions spike.

``run_id`` policy (spike evaluation only; not the production Hatpin engine):

- Non-empty string, at most :data:`SPIKE_RUN_ID_MAX_LEN` characters.
- **ASCII only:** letters, digits, hyphen (``-``), underscore (``_``),
  dot (``.``), hash (``#``). No slashes or whitespace (filenames are
  ``{run_id}.json`` under :func:`spike_state_dir`).
- Must not contain the substring ``..``.

Every path derived from ``run_id`` (checkpoint JSON, ``resume.*.flag``,
``pause_key`` segments) must go through :func:`validate_spike_run_id` /
:func:`safe_spike_run_segment` so validation rules stay centralized.
"""

from __future__ import annotations

import os
from pathlib import Path

SPIKE_RUN_ID_MAX_LEN = 128


def validate_spike_run_id(run_id: str) -> str:
    """Return *run_id* after validating spike rules, or raise ``ValueError``.

    See module docstring for the canonical policy. Callers should use this (or
    :func:`safe_spike_run_segment`, which delegates here) before building any
    filesystem path or parsing gate ``pause_key`` run segments.
    """
    if not isinstance(run_id, str):
        raise ValueError("run_id must be a str")
    if not run_id:
        raise ValueError("run_id must be non-empty")
    if len(run_id) > SPIKE_RUN_ID_MAX_LEN:
        raise ValueError(
            f"run_id exceeds maximum length ({SPIKE_RUN_ID_MAX_LEN} characters)"
        )
    if ".." in run_id:
        raise ValueError("run_id must not contain '..' (parent-directory segments)")
    for i, ch in enumerate(run_id):
        o = ord(ch)
        if o > 127:
            raise ValueError(
                f"run_id must be ASCII only; non-ASCII at index {i}: {ch!r}"
            )
        if ch.isalnum() or ch in "-_.#":
            continue
        raise ValueError(
            f"run_id contains disallowed character {ch!r} at index {i}; "
            "allowed: ASCII letters, digits, hyphen, underscore, dot, hash"
        )
    return run_id


def safe_spike_run_segment(run_id: str) -> str:
    """Return *run_id* unchanged after :func:`validate_spike_run_id` (filename segment)."""
    return validate_spike_run_id(run_id)


def spike_state_dir() -> Path:
    override = os.environ.get("HATPIN_SPIKE_STATE_DIR")
    if override:
        return Path(override)
    return Path.cwd() / ".hatpin" / "spikes" / "huey_transitions"
