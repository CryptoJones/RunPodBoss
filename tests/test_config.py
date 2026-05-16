# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Tests for config loading + validation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runpodboss.config import Config, load_config


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _base(api_key: str = "test-key") -> dict:
    return {
        "api_key": api_key,
        "thresholds": [
            {"name": "warning", "below_usd": 10.0, "prompt": "ping {balance}"},
        ],
    }


def test_load_minimal_config_succeeds(tmp_path):
    p = _write(tmp_path, _base())
    cfg = load_config(p)
    assert isinstance(cfg, Config)
    assert cfg.api_key == "test-key"
    assert len(cfg.thresholds) == 1
    assert cfg.thresholds[0].name == "warning"


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.json")


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json{", encoding="utf-8")
    with pytest.raises(ValueError, match="valid JSON"):
        load_config(p)


def test_missing_api_key_raises_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    payload = _base(api_key="")
    p = _write(tmp_path, payload)
    with pytest.raises(ValueError, match="API key is required"):
        load_config(p)


def test_api_key_resolved_from_default_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "key-from-env")
    p = _write(tmp_path, _base(api_key=""))
    cfg = load_config(p)
    assert cfg.api_key == "key-from-env"


def test_api_key_resolved_from_custom_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_CUSTOM_KEY", "custom-key")
    p = _write(tmp_path, {**_base(api_key=""), "api_key_env": "MY_CUSTOM_KEY"})
    cfg = load_config(p)
    assert cfg.api_key == "custom-key"


def test_thresholds_sorted_high_to_low(tmp_path):
    payload = _base()
    payload["thresholds"] = [
        {"name": "critical", "below_usd": 2.0, "prompt": "x"},
        {"name": "warning", "below_usd": 10.0, "prompt": "x"},
        {"name": "emergency", "below_usd": 0.5, "prompt": "x"},
    ]
    p = _write(tmp_path, payload)
    cfg = load_config(p)
    assert [t.name for t in cfg.thresholds] == ["warning", "critical", "emergency"]


def test_empty_thresholds_rejected(tmp_path):
    p = _write(tmp_path, {**_base(), "thresholds": []})
    with pytest.raises(ValueError, match="non-empty list"):
        load_config(p)


def test_threshold_below_zero_rejected(tmp_path):
    payload = _base()
    payload["thresholds"][0]["below_usd"] = -1
    p = _write(tmp_path, payload)
    with pytest.raises(ValueError, match="below_usd"):
        load_config(p)


def test_threshold_missing_prompt_rejected(tmp_path):
    payload = _base()
    payload["thresholds"][0]["prompt"] = ""
    p = _write(tmp_path, payload)
    with pytest.raises(ValueError, match="prompt_template"):
        load_config(p)


def test_poll_interval_floor(tmp_path):
    p = _write(tmp_path, {**_base(), "poll_interval_seconds": 2})
    with pytest.raises(ValueError, match="at least 5"):
        load_config(p)


def test_max_runtime_must_be_non_negative(tmp_path):
    p = _write(tmp_path, {**_base(), "max_runtime_seconds": -1})
    with pytest.raises(ValueError, match="max_runtime_seconds"):
        load_config(p)


def test_claude_command_default(tmp_path):
    p = _write(tmp_path, _base())
    cfg = load_config(p)
    assert cfg.claude_command == ("claude", "-p")


def test_claude_command_override(tmp_path):
    p = _write(tmp_path, {**_base(), "claude_command": ["claude", "--print"]})
    cfg = load_config(p)
    assert cfg.claude_command == ("claude", "--print")


def test_claude_command_must_be_list_of_strings(tmp_path):
    p = _write(tmp_path, {**_base(), "claude_command": "claude -p"})
    with pytest.raises(ValueError, match="claude_command"):
        load_config(p)
