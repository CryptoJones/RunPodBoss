# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Config loading + validation for RunPodBoss.

The config file is a single JSON document. See `config.example.json`
in the repo root for the canonical schema + commentary in the README.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("~/.runpodboss/config.json").expanduser()
DEFAULT_STATE_PATH = Path("~/.runpodboss/state.json").expanduser()
DEFAULT_LOG_PATH = Path("~/.runpodboss/runpodboss.log").expanduser()
DEFAULT_POLL_INTERVAL_SECONDS = 60


@dataclass(frozen=True)
class Threshold:
    """A single below-USD trip wire.

    `prompt_template` is rendered with `.format(balance=..., pods_json=...)`
    just before being passed to `claude -p`. Keep the template explicit
    about what you want the agent to do — Claude's "ping" should be a
    clear instruction, not a vague heads-up.
    """

    name: str
    below_usd: float
    prompt_template: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Threshold needs a name.")
        if self.below_usd <= 0:
            raise ValueError(f"Threshold {self.name!r}: below_usd must be > 0.")
        if not self.prompt_template:
            raise ValueError(f"Threshold {self.name!r}: prompt_template is required.")


@dataclass(frozen=True)
class Config:
    api_key: str
    poll_interval_seconds: int
    thresholds: tuple[Threshold, ...]
    state_file: Path
    log_file: Path
    claude_command: tuple[str, ...] = ("claude", "-p")
    # Skip-network mode for smoke-testing the loop without burning API quota.
    dry_run: bool = False
    # Optional: bound the daemon's lifetime. Hard ceiling — if the watcher
    # outlives this duration something went wrong; exit before we become
    # the problem. 0 = unbounded (default).
    max_runtime_seconds: int = 0
    # When non-empty, also run this shell command on every threshold trip
    # in addition to the Claude ping. Useful for a desktop notify-send /
    # Pushover / Slack webhook without baking those into RunPodBoss.
    extra_notify_command: tuple[str, ...] = field(default_factory=tuple)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> Config:
    """Load and validate the config JSON. Raises ValueError on any problem.

    The RunPod API key resolution order:
      1. Explicit `api_key` in the JSON.
      2. `api_key_env` in the JSON points at an env var name; read that var.
      3. RUNPOD_API_KEY env var.
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"Config file not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Config file is not valid JSON: {e}") from e
    if not isinstance(raw, dict):
        raise ValueError(f"Config root must be a JSON object, got {type(raw).__name__}.")

    api_key = raw.get("api_key") or ""
    if not api_key:
        env_name = raw.get("api_key_env") or "RUNPOD_API_KEY"
        api_key = os.environ.get(env_name, "")
    if not api_key:
        raise ValueError(
            "RunPod API key is required: set 'api_key' in config.json, or set "
            "the env var named in 'api_key_env' (default RUNPOD_API_KEY)."
        )

    raw_thresholds = raw.get("thresholds")
    if not isinstance(raw_thresholds, list) or not raw_thresholds:
        raise ValueError("'thresholds' must be a non-empty list.")
    thresholds = tuple(
        Threshold(
            name=str(t.get("name", "")),
            below_usd=float(t.get("below_usd", 0)),
            prompt_template=str(t.get("prompt", "")),
        )
        for t in raw_thresholds
    )
    # Fire-order: highest balance first, so a crossing from $9 → $1 trips
    # both "warning" ($10) and "critical" ($2) in the right order on a
    # single poll cycle.
    thresholds = tuple(sorted(thresholds, key=lambda t: -t.below_usd))

    poll = int(raw.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS))
    if poll < 5:
        raise ValueError("poll_interval_seconds must be at least 5.")

    state_path = Path(raw.get("state_file") or DEFAULT_STATE_PATH).expanduser()
    log_path = Path(raw.get("log_file") or DEFAULT_LOG_PATH).expanduser()

    claude_cmd_raw = raw.get("claude_command")
    if claude_cmd_raw is None:
        claude_cmd: tuple[str, ...] = ("claude", "-p")
    elif isinstance(claude_cmd_raw, list) and all(isinstance(x, str) for x in claude_cmd_raw):
        claude_cmd = tuple(claude_cmd_raw)
    else:
        raise ValueError("'claude_command' must be a list of strings (e.g. ['claude', '-p']).")

    extra_notify_raw = raw.get("extra_notify_command") or []
    if not isinstance(extra_notify_raw, list) or not all(
        isinstance(x, str) for x in extra_notify_raw
    ):
        raise ValueError("'extra_notify_command' must be a list of strings.")
    extra_notify = tuple(extra_notify_raw)

    max_runtime = int(raw.get("max_runtime_seconds", 0))
    if max_runtime < 0:
        raise ValueError("max_runtime_seconds must be >= 0 (0 = unbounded).")

    return Config(
        api_key=api_key,
        poll_interval_seconds=poll,
        thresholds=thresholds,
        state_file=state_path,
        log_file=log_path,
        claude_command=claude_cmd,
        dry_run=bool(raw.get("dry_run", False)),
        max_runtime_seconds=max_runtime,
        extra_notify_command=extra_notify,
    )
