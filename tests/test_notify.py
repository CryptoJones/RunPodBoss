# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Tests for prompt rendering + subprocess spawn behavior."""

from __future__ import annotations

import json
import subprocess

import pytest

from runpodboss.config import Threshold
from runpodboss.notify import fire_claude_ping, fire_extra_notify, render_prompt
from runpodboss.runpod_api import Pod


def _t(template: str) -> Threshold:
    return Threshold(name="warning", below_usd=10.0, prompt_template=template)


def _pods() -> list[Pod]:
    return [
        Pod(id="abc", name="training", cost_per_hr=1.49, desired_status="RUNNING"),
        Pod(id="def", name="idle", cost_per_hr=0.34, desired_status="EXITED"),
    ]


def test_render_prompt_substitutes_balance():
    out = render_prompt(_t("Balance is ${balance:.2f}"), 3.5, [])
    assert out == "Balance is $3.50"


def test_render_prompt_substitutes_pods_json():
    out = render_prompt(_t("Pods: {pods_json}"), 5.0, _pods())
    assert '"id": "abc"' in out
    assert '"cost_per_hr_usd": 1.49' in out
    assert '"status": "RUNNING"' in out


def test_render_prompt_is_valid_json_for_pods_only_template():
    out = render_prompt(_t("{pods_json}"), 5.0, _pods())
    parsed = json.loads(out)
    assert parsed[0]["id"] == "abc"
    assert parsed[1]["status"] == "EXITED"


class _FakeRun:
    """Capture subprocess.run argv + simulate a return code."""

    def __init__(self, returncode: int = 0, stderr: str = ""):
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, text, capture_output, check):
        # Match the signature subprocess.run is called with by notify.py.
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.returncode, stdout="", stderr=self.stderr)


def test_fire_claude_ping_passes_prompt_as_final_argv():
    fake = _FakeRun()
    fire_claude_ping(
        _t("balance is {balance}"),
        7.0,
        _pods(),
        ("claude", "-p"),
        _spawn=fake,
    )
    assert len(fake.calls) == 1
    argv = fake.calls[0]
    assert argv[:2] == ["claude", "-p"]
    assert argv[2] == "balance is 7.0"


def test_fire_claude_ping_uses_custom_command():
    fake = _FakeRun()
    fire_claude_ping(
        _t("x"),
        1.0,
        [],
        ("/usr/local/bin/claude", "--print", "--quiet"),
        _spawn=fake,
    )
    assert fake.calls[0][:3] == ["/usr/local/bin/claude", "--print", "--quiet"]


def test_fire_claude_ping_returns_completed_process_on_nonzero():
    fake = _FakeRun(returncode=5, stderr="boom")
    result = fire_claude_ping(_t("x"), 1.0, [], ("claude", "-p"), _spawn=fake)
    assert result.returncode == 5
    assert "boom" in result.stderr


def test_fire_extra_notify_returns_none_when_unconfigured():
    fake = _FakeRun()
    result = fire_extra_notify((), _t("x"), 1.0, _spawn=fake)
    assert result is None
    assert fake.calls == []


def test_fire_extra_notify_appends_threshold_and_balance():
    fake = _FakeRun()
    fire_extra_notify(("notify-send", "RunPod"), _t("x"), 3.14, _spawn=fake)
    argv = fake.calls[0]
    assert argv[0] == "notify-send"
    assert argv[1] == "RunPod"
    assert argv[2] == "warning"  # threshold name
    assert argv[3] == "3.1400"   # balance formatted


def test_threshold_below_zero_rejected():
    with pytest.raises(ValueError):
        Threshold(name="x", below_usd=-1.0, prompt_template="y")
