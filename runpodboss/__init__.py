# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""RunPodBoss — RunPod credit guardrail for Claude agents.

A small daemon that polls RunPod balance + running pods on an interval, and
when configured thresholds are crossed, fires a `claude -p ...` subprocess
with the current state so a Claude agent can decide whether to terminate
running pods before the balance hits zero.
"""

__version__ = "0.1.0"
