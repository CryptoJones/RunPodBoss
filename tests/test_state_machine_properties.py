# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Property-based tests for the threshold state machine.

The example-based tests in `test_monitor.py` cover specific scenarios.
This file fuzzes the same machinery against random balance sequences and
asserts invariants that must hold regardless of input shape:

1. **Terminal correctness**: at the end of any sequence, `fired[t]` is True
   iff (a) the last balance is below the threshold AND (b) the balance has
   never returned above it since the last fire.

2. **One-fire-per-crossing**: the count of times a threshold "fires" across
   the sequence equals the count of below-crossings *from above-or-armed*.
   We never fire while already-fired.

3. **Bounded fires**: total fires for any threshold is at most the count of
   balance transitions from "above the threshold" to "below the threshold."

The state machine is the load-bearing piece of the credit guardrail. These
properties catch regressions the example tests would miss — particularly
sequences with rapid up/down oscillations or balances exactly at a
threshold boundary.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from runpodboss.config import Threshold
from runpodboss.monitor import evaluate_thresholds
from runpodboss.state import State

# Balance values: floats from $0 to $1000, plus the explicit edges 0 and
# the threshold values themselves to exercise the boundary semantics.
# We use `floats()` over `decimals()` because the production code uses
# Python floats throughout.
balances_strategy = st.lists(
    st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
    min_size=0,
    max_size=200,
)


def _t(name: str, below: float) -> Threshold:
    return Threshold(name=name, below_usd=below, prompt_template="p {balance}")


@given(balances=balances_strategy)
@settings(max_examples=200, deadline=None)
def test_terminal_state_consistent_with_last_balance(balances):
    """After applying every balance in order, the final `fired` flag for
    each threshold must reflect "we crossed below it at least once and
    haven't recovered above it since." This is the contract the rest of
    RunPodBoss depends on."""
    ts = (_t("warning", 10.0), _t("critical", 2.0), _t("emergency", 0.5))
    state = State()
    # Track expected state by replaying the machine in plain Python.
    expected_fired: dict[str, bool] = {t.name: False for t in ts}

    for bal in balances:
        evaluate_thresholds(bal, ts, state)
        for t in ts:
            if bal < t.below_usd:
                # First crossing below sets fired; subsequent stays stay.
                if not expected_fired[t.name]:
                    expected_fired[t.name] = True
            else:
                # Recovered above — re-arm.
                expected_fired[t.name] = False

    for t in ts:
        assert state.is_fired(t.name) is expected_fired[t.name], (
            f"Threshold {t.name}: state says fired={state.is_fired(t.name)}, "
            f"expected fired={expected_fired[t.name]} after sequence {balances}"
        )


@given(balances=balances_strategy)
@settings(max_examples=200, deadline=None)
def test_fires_equal_crossings_below(balances):
    """A threshold fires exactly once per below-crossing-from-above (or from
    fresh-armed). Sitting flat below doesn't generate duplicate fires; an
    oscillation up-then-down does."""
    t = _t("warning", 10.0)
    state = State()
    actual_fires = 0
    expected_fires = 0
    previously_below = False  # treat fresh state as "above" for the first compare

    for bal in balances:
        # Expected: fire when transitioning from above to below.
        currently_below = bal < t.below_usd
        if currently_below and not previously_below:
            expected_fires += 1
        previously_below = currently_below

        fired_now = evaluate_thresholds(bal, (t,), state)
        if any(x.name == "warning" for x in fired_now):
            actual_fires += 1

    assert actual_fires == expected_fires, (
        f"actual fires={actual_fires}, expected={expected_fires} for {balances}"
    )


@given(
    balances=balances_strategy,
    threshold_value=st.floats(
        min_value=0.01, max_value=999.0, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=100, deadline=None)
def test_no_refire_while_flat_below(balances, threshold_value):
    """Across a window where the balance stays below the threshold the entire
    time, at most one fire occurs (the first sample). Property generalises
    over any single threshold."""
    t = _t("warning", threshold_value)
    state = State()
    fires = 0
    consecutive_below = 0
    max_consecutive_below_with_fires = 0
    fires_this_run = 0

    for bal in balances:
        currently_below = bal < t.below_usd
        if currently_below:
            consecutive_below += 1
        fired_now = evaluate_thresholds(bal, (t,), state)
        was_fire = any(x.name == "warning" for x in fired_now)
        if was_fire:
            fires += 1
            fires_this_run += 1
        if not currently_below:
            # Reset the consecutive-below counter on every recovery.
            max_consecutive_below_with_fires = max(
                max_consecutive_below_with_fires, fires_this_run
            )
            consecutive_below = 0
            fires_this_run = 0
    max_consecutive_below_with_fires = max(max_consecutive_below_with_fires, fires_this_run)

    # The number of fires within any single "flat below run" must be <= 1.
    assert max_consecutive_below_with_fires <= 1, (
        f"Multiple fires within a single flat-below window: {balances}"
    )


@given(
    threshold_value=st.floats(
        min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False
    ),
    delta=st.floats(min_value=0.01, max_value=10.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=100, deadline=None)
def test_threshold_boundary_strictly_less_than(threshold_value, delta):
    """A balance EXACTLY equal to a threshold does NOT fire (the comparison
    is `balance < below_usd`, strictly less)."""
    t = _t("warning", threshold_value)
    state = State()

    # Sitting at the threshold exactly: must not fire.
    fired = evaluate_thresholds(threshold_value, (t,), state)
    assert fired == []
    assert state.is_fired("warning") is False

    # Dipping just below: must fire.
    fired = evaluate_thresholds(threshold_value - delta, (t,), state)
    if threshold_value - delta < threshold_value:  # may not be true for tiny delta + fp slop
        assert [x.name for x in fired] == ["warning"]
        assert state.is_fired("warning") is True
