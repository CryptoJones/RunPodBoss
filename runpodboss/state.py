# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Threshold-armed-state persistence.

Each named threshold is in one of two states:

  * **armed**: the last poll saw the balance ABOVE this threshold. Crossing
    below will fire the action and flip to fired.
  * **fired**: the action has already been fired since the last time the
    balance was above this threshold. Don't re-fire (otherwise a flat-line
    balance below threshold would spam the Claude agent every poll cycle).

Re-arming happens when the balance returns above the threshold (a top-up,
or balance ticked up because pods were terminated). That's the cycle.

State is persisted to a small JSON file so a restart of the daemon doesn't
re-fire on every threshold below the current balance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class State:
    """Mutable armed/fired state, keyed by threshold name.

    A threshold name that has never been seen is implicitly "armed" —
    i.e., a first-launch where balance is already below a threshold will
    fire once and then stay fired until the balance recovers above it.
    """

    fired: dict[str, bool] = field(default_factory=dict)

    def is_fired(self, name: str) -> bool:
        return bool(self.fired.get(name))

    def mark_fired(self, name: str) -> None:
        self.fired[name] = True

    def mark_armed(self, name: str) -> None:
        # Use False rather than `del` so the key is preserved for diagnostics
        # (operator can see "this threshold has been observed at least once").
        self.fired[name] = False


def load_state(path: Path) -> State:
    """Load state from disk; missing file returns a fresh State."""
    if not path.exists():
        return State()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Corrupt state file: don't crash the watcher, just start clean.
        # Worst case is a duplicate fire on the next threshold crossing,
        # which is much better than the watcher refusing to run.
        return State()
    fired = raw.get("fired", {})
    if not isinstance(fired, dict):
        return State()
    return State(fired={str(k): bool(v) for k, v in fired.items()})


def save_state(state: State, path: Path) -> None:
    """Persist state to disk. Creates parent directory if needed.

    Writes via a tmp-file-then-rename so a crash mid-write can't leave the
    state file half-written and unreadable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"fired": state.fired}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)
