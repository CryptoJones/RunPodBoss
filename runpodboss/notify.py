# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Fire a Claude-Code-headless `claude -p` subprocess (and optionally a
shell extra-notify command) when a threshold trips.

The Claude agent receives a fully-rendered prompt containing the current
balance and a JSON list of all pods. It can then use its Bash tool to call
`runpodctl pod delete <id>` for any pod it judges expendable.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterable

from .config import Threshold
from .runpod_api import Pod

logger = logging.getLogger(__name__)


def render_prompt(threshold: Threshold, balance_usd: float, pods: Iterable[Pod]) -> str:
    """Render the prompt template with the live balance + pod list.

    Two placeholders are accepted:
      {balance}    — the current balance in USD as a float (e.g. 1.74).
      {pods_json}  — a pretty-printed JSON array of pods.

    Any placeholder the template doesn't reference is harmlessly ignored.
    """
    pods_serialised = [
        {
            "id": p.id,
            "name": p.name,
            "cost_per_hr_usd": p.cost_per_hr,
            "status": p.desired_status,
        }
        for p in pods
    ]
    return threshold.prompt_template.format(
        balance=balance_usd,
        pods_json=json.dumps(pods_serialised, indent=2),
    )


def fire_claude_ping(
    threshold: Threshold,
    balance_usd: float,
    pods: Iterable[Pod],
    claude_command: tuple[str, ...],
    *,
    runner: subprocess._CompletedProcess[str] | None = None,
    _spawn=subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """Spawn `claude -p <rendered-prompt>` and return the CompletedProcess.

    Does NOT raise on non-zero exit: a Claude-side error shouldn't kill the
    watcher (we still want subsequent thresholds to fire). The caller logs
    the return code and stderr.

    Args:
        threshold: The threshold being tripped.
        balance_usd: Current RunPod balance.
        pods: Pods to show the agent.
        claude_command: argv prefix, default ('claude', '-p').
        _spawn: subprocess.run, exposed for tests.
    """
    prompt = render_prompt(threshold, balance_usd, pods)
    argv = (*claude_command, prompt)
    logger.info("Firing Claude ping for threshold %s (balance=$%.2f)", threshold.name, balance_usd)
    return _spawn(
        list(argv),
        text=True,
        capture_output=True,
        check=False,
    )


def fire_extra_notify(
    extra_command: tuple[str, ...],
    threshold: Threshold,
    balance_usd: float,
    *,
    _spawn=subprocess.run,
) -> subprocess.CompletedProcess[str] | None:
    """If extra-notify is configured, fire it with two trailing args:
    threshold name, current balance. Returns None when no command is set.

    Convention: the operator provides a shell command (e.g. notify-send,
    curl-to-a-webhook). We append `<threshold_name>` and `<balance>` so the
    user can structure their own message however they like.
    """
    if not extra_command:
        return None
    argv = (*extra_command, threshold.name, f"{balance_usd:.4f}")
    logger.info("Firing extra-notify command: %s", " ".join(argv))
    return _spawn(
        list(argv),
        text=True,
        capture_output=True,
        check=False,
    )
