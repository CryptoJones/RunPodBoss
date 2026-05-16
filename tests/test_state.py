# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Tests for the persisted threshold-armed state."""

from __future__ import annotations

import json

from runpodboss.state import State, load_state, save_state


def test_fresh_state_has_no_fired(tmp_path):
    s = load_state(tmp_path / "missing.json")
    assert s.fired == {}
    assert s.is_fired("anything") is False


def test_mark_fired_persists_round_trip(tmp_path):
    p = tmp_path / "state.json"
    s = State()
    s.mark_fired("warning")
    save_state(s, p)
    s2 = load_state(p)
    assert s2.is_fired("warning") is True
    assert s2.is_fired("critical") is False


def test_corrupt_state_file_falls_back_to_fresh(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("this is not json", encoding="utf-8")
    s = load_state(p)
    assert s.fired == {}


def test_non_dict_fired_falls_back_to_fresh(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"fired": "not-a-dict"}), encoding="utf-8")
    s = load_state(p)
    assert s.fired == {}


def test_mark_armed_clears_fired_flag(tmp_path):
    s = State()
    s.mark_fired("critical")
    assert s.is_fired("critical") is True
    s.mark_armed("critical")
    assert s.is_fired("critical") is False
    # Key is preserved (not deleted) — useful for diagnostics.
    assert "critical" in s.fired


def test_save_state_creates_parent_dir(tmp_path):
    p = tmp_path / "deep" / "nested" / "state.json"
    save_state(State(fired={"a": True}), p)
    assert p.is_file()
    loaded = load_state(p)
    assert loaded.is_fired("a") is True


def test_save_state_atomic_replace(tmp_path):
    """save_state writes via tmp + rename so a crash mid-write can't corrupt
    the existing state file. The tmp file should not linger after a successful
    write."""
    p = tmp_path / "state.json"
    save_state(State(fired={"a": True}), p)
    save_state(State(fired={"a": True, "b": True}), p)
    # No leftover .tmp file.
    assert not any(child.name.endswith(".tmp") for child in tmp_path.iterdir())
