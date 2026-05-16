# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""The poll loop: fetch balance + pods, evaluate every threshold, fire when crossed.

Design notes:

  * Thresholds are evaluated highest-balance-first, so going from $9 to $1
    in a single poll trips both warning AND critical (and fires both pings)
    in the right order.
  * Once a threshold has fired, we don't re-fire it until the balance has
    *recovered above* the threshold. That handles the flat-line-below case
    (balance sits at $1.50 for an hour — we ping ONCE, not every minute).
  * Errors talking to the RunPod API are logged but DO NOT crash the loop.
    A transient network blip should not silently turn off the guardrail.
  * A hard `max_runtime_seconds` ceiling on the loop is supported. When the
    deadline arrives, we exit cleanly — the operator should be running this
    under a supervisor (systemd / tmux) that restarts it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .config import Config, Threshold
from .notify import fire_claude_ping, fire_extra_notify
from .runpod_api import Pod, RunPodAPIError, RunPodClient
from .state import State, load_state, save_state

logger = logging.getLogger(__name__)


def evaluate_thresholds(
    balance_usd: float,
    thresholds: tuple[Threshold, ...],
    state: State,
) -> list[Threshold]:
    """Mutate `state`. Return the list of thresholds that fired this cycle.

    Fire when crossing below; re-arm when balance recovers above. Order
    of the returned list mirrors the input (which the loader sorts
    highest-balance-first).
    """
    fired: list[Threshold] = []
    for t in thresholds:
        currently_below = balance_usd < t.below_usd
        already_fired = state.is_fired(t.name)
        if currently_below and not already_fired:
            fired.append(t)
            state.mark_fired(t.name)
        elif not currently_below and already_fired:
            # Recovered above — re-arm so the next dip below fires again.
            state.mark_armed(t.name)
    return fired


def poll_once(
    client: RunPodClient,
    cfg: Config,
    state: State,
    *,
    fire_ping: Callable[..., None] = fire_claude_ping,  # noqa: ARG001 — testable seam
    fire_extra: Callable[..., None] = fire_extra_notify,  # noqa: ARG001
) -> tuple[float | None, list[Pod], list[Threshold]]:
    """One poll cycle. Returns (balance, pods, fired_thresholds).

    On API failure, returns (None, [], []) and logs — does not raise.
    """
    try:
        balance = client.get_balance_usd()
        pods = client.list_pods()
    except RunPodAPIError as e:
        logger.warning("RunPod API call failed; skipping this poll: %s", e)
        return None, [], []

    fired = evaluate_thresholds(balance, cfg.thresholds, state)
    for t in fired:
        try:
            result = fire_ping(t, balance, pods, cfg.claude_command)
            if result and result.returncode != 0:
                logger.warning(
                    "Claude ping for %s exited %d: %s",
                    t.name,
                    result.returncode,
                    (result.stderr or "").strip()[:500],
                )
        except OSError as e:
            # `claude` binary missing, permission denied, etc. Don't crash.
            logger.error("Failed to spawn Claude ping for %s: %s", t.name, e)
        try:
            fire_extra(cfg.extra_notify_command, t, balance)
        except OSError as e:
            logger.error("Failed to run extra-notify for %s: %s", t.name, e)
    return balance, pods, fired


def run_loop(
    cfg: Config,
    *,
    client: RunPodClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    poll_once_fn: Callable[..., tuple[float | None, list[Pod], list[Threshold]]] = poll_once,
) -> int:
    """Main daemon entry. Returns an exit code (0 on graceful stop)."""
    if client is None:
        client = RunPodClient(cfg.api_key, url=cfg.runpod_url)
    state = load_state(cfg.state_file)
    started = now()
    cycle = 0

    logger.info(
        "RunPodBoss watching: %d threshold(s), poll every %ds, max_runtime=%ds (0=unbounded)",
        len(cfg.thresholds),
        cfg.poll_interval_seconds,
        cfg.max_runtime_seconds,
    )

    while True:
        cycle += 1
        balance, _pods, fired = poll_once_fn(client, cfg, state)
        if balance is not None:
            names = ", ".join(t.name for t in fired) or "-"
            logger.info("cycle=%d balance=$%.4f fired=[%s]", cycle, balance, names)
        save_state(state, cfg.state_file)

        if cfg.max_runtime_seconds and now() - started >= cfg.max_runtime_seconds:
            logger.info("max_runtime_seconds reached; exiting cleanly.")
            return 0

        sleep(cfg.poll_interval_seconds)
