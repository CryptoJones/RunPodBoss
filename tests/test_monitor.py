# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Tests for the threshold-evaluation logic + the poll loop's safety properties.

The loop is fully exercised via dependency injection: a fake RunPodClient
plus a captured `fire_ping` callable plus a controllable `sleep` and
`now`. No real network, no real subprocess, no real sleeping.
"""

from __future__ import annotations

import subprocess
from typing import Any

from runpodboss.config import Config, Threshold
from runpodboss.monitor import evaluate_thresholds, poll_once, run_loop
from runpodboss.runpod_api import Pod, RunPodAPIError
from runpodboss.state import State


def _threshold(name: str, below: float) -> Threshold:
    return Threshold(name=name, below_usd=below, prompt_template="ping {balance}")


# evaluate_thresholds is the heart of the guardrail — exhaustive tests here.


def test_evaluate_no_thresholds_no_firing():
    s = State()
    fired = evaluate_thresholds(5.0, (), s)
    assert fired == []
    assert s.fired == {}


def test_evaluate_balance_above_all_thresholds_no_firing():
    s = State()
    fired = evaluate_thresholds(
        50.0,
        (_threshold("warning", 10.0), _threshold("critical", 2.0)),
        s,
    )
    assert fired == []


def test_evaluate_balance_below_one_threshold_fires_it():
    s = State()
    t_warn = _threshold("warning", 10.0)
    fired = evaluate_thresholds(7.0, (t_warn,), s)
    assert [t.name for t in fired] == ["warning"]
    assert s.is_fired("warning") is True


def test_evaluate_below_multiple_thresholds_fires_all_in_one_cycle():
    s = State()
    ts = (_threshold("warning", 10.0), _threshold("critical", 2.0), _threshold("emergency", 0.5))
    fired = evaluate_thresholds(0.30, ts, s)
    assert [t.name for t in fired] == ["warning", "critical", "emergency"]
    assert all(s.is_fired(name) for name in ("warning", "critical", "emergency"))


def test_evaluate_does_not_refire_same_threshold():
    """Sitting below the threshold for many polls fires the ping exactly once
    until the balance recovers above it. Otherwise we'd spam the Claude
    agent every poll interval."""
    s = State()
    t = _threshold("warning", 10.0)
    fired_first = evaluate_thresholds(7.0, (t,), s)
    fired_second = evaluate_thresholds(6.0, (t,), s)
    fired_third = evaluate_thresholds(5.0, (t,), s)
    assert len(fired_first) == 1
    assert fired_second == []
    assert fired_third == []


def test_evaluate_rearms_when_balance_recovers_above_threshold():
    """A top-up that pushes the balance back above a threshold re-arms it
    so the next dip below fires again."""
    s = State()
    t = _threshold("warning", 10.0)
    evaluate_thresholds(7.0, (t,), s)  # fire
    evaluate_thresholds(15.0, (t,), s)  # re-arm
    refired = evaluate_thresholds(8.0, (t,), s)  # fire again
    assert [x.name for x in refired] == ["warning"]


def test_evaluate_recovery_above_one_threshold_only_rearms_that_one():
    s = State()
    ts = (_threshold("warning", 10.0), _threshold("critical", 2.0))
    evaluate_thresholds(1.0, ts, s)  # fires both
    assert s.is_fired("warning") and s.is_fired("critical")
    evaluate_thresholds(5.0, ts, s)  # back above critical only
    assert s.is_fired("warning") is True  # still below 10
    assert s.is_fired("critical") is False  # re-armed


# poll_once: the full per-cycle integration.


class _FakeClient:
    def __init__(self, balance, pods, raise_exc=None):
        self._balance = balance
        self._pods = pods
        self._raise = raise_exc
        self.balance_calls = 0
        self.pods_calls = 0

    def get_balance_usd(self):
        self.balance_calls += 1
        if self._raise:
            raise self._raise
        return self._balance

    def list_pods(self):
        self.pods_calls += 1
        if self._raise:
            raise self._raise
        return self._pods


def _cfg(thresholds, extra_notify=()):
    return Config(
        api_key="k",
        poll_interval_seconds=60,
        thresholds=tuple(thresholds),
        state_file=None,  # type: ignore[arg-type]
        log_file=None,  # type: ignore[arg-type]
        claude_command=("claude", "-p"),
        extra_notify_command=extra_notify,
    )


def test_poll_once_fires_ping_for_crossed_threshold():
    spawned: list[Any] = []

    def fake_ping(t, bal, pods, cmd):
        spawned.append((t.name, bal, cmd))
        return subprocess.CompletedProcess([], 0, "", "")

    client = _FakeClient(balance=5.0, pods=[Pod("a", "n", 0.5, "RUNNING")])
    cfg = _cfg([_threshold("warning", 10.0)])
    state = State()
    bal, pods, fired = poll_once(
        client, cfg, state, fire_ping=fake_ping, fire_extra=lambda *a, **k: None
    )
    assert bal == 5.0
    assert len(pods) == 1
    assert [t.name for t in fired] == ["warning"]
    assert spawned == [("warning", 5.0, ("claude", "-p"))]


def test_poll_once_api_failure_returns_empty_and_does_not_fire():
    spawned: list[Any] = []

    def fake_ping(*a, **k):
        spawned.append(a)
        return subprocess.CompletedProcess([], 0, "", "")

    client = _FakeClient(balance=0.0, pods=[], raise_exc=RunPodAPIError("transient"))
    cfg = _cfg([_threshold("warning", 10.0)])
    state = State()
    bal, pods, fired = poll_once(
        client, cfg, state, fire_ping=fake_ping, fire_extra=lambda *a, **k: None
    )
    assert bal is None
    assert pods == []
    assert fired == []
    assert spawned == []
    # And state must not have flipped — a transient API blip can't
    # silently consume a threshold's one-shot fire.
    assert state.fired == {}


def test_poll_once_ping_spawn_failure_does_not_kill_loop():
    """An OSError from spawning `claude` (e.g. missing binary) must be logged
    but not propagated. The watcher continuing to run is itself a guardrail."""

    def boom_ping(*a, **k):
        raise OSError("claude not on PATH")

    client = _FakeClient(balance=3.0, pods=[])
    cfg = _cfg([_threshold("critical", 5.0)])
    state = State()
    bal, _pods, fired = poll_once(
        client, cfg, state, fire_ping=boom_ping, fire_extra=lambda *a, **k: None
    )
    assert bal == 3.0
    # Threshold still considered "fired" — we recorded the crossing even
    # though the ping failed to spawn. Otherwise we'd hammer the broken
    # binary every poll cycle.
    assert state.is_fired("critical") is True
    assert [t.name for t in fired] == ["critical"]


# run_loop: hard-ceiling exit + driver wiring.


def test_run_loop_respects_max_runtime_seconds():
    """The hard-ceiling exit is the load-bearing safety property — it's the
    catch-all for "the watcher kept running but something went wrong." If
    a deadline is set, the loop must terminate at or after it regardless
    of what the inner state says."""

    from pathlib import Path
    cfg = Config(
        api_key="k",
        poll_interval_seconds=30,
        thresholds=(_threshold("warning", 10.0),),
        state_file=Path("/tmp/runpodboss-test-state.json"),
        log_file=Path("/tmp/runpodboss-test.log"),
        claude_command=("claude", "-p"),
        max_runtime_seconds=10,
    )

    # Fake clock that advances by 6 seconds per now() call.
    clock = [0.0]

    def fake_now() -> float:
        clock[0] += 6
        return clock[0]

    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    def fake_poll(client, cfg_, state):
        return 5.0, [], []  # below warning, doesn't matter — no fire path taken

    fake_client = _FakeClient(balance=5.0, pods=[])
    rc = run_loop(
        cfg,
        client=fake_client,
        sleep=fake_sleep,
        now=fake_now,
        poll_once_fn=fake_poll,
    )
    assert rc == 0
    # Must have slept at least once before bailing, but not run forever.
    assert 1 <= len(sleep_calls) <= 5
