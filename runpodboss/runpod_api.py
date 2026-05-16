# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Minimal RunPod GraphQL client.

Two queries we care about for guardrail use:

  1. clientBalance — the operator's remaining credit, USD.
  2. myself.pods  — running pods with id, name, cost/hr, status.

Stdlib only (urllib + json) so RunPodBoss itself has zero pip dependencies.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

RUNPOD_GRAPHQL_URL = "https://api.runpod.io/graphql"

# 30 seconds is plenty for a small GraphQL request and short enough that
# a network blip can't stall the watcher indefinitely.
DEFAULT_TIMEOUT_SECONDS = 30


class RunPodAPIError(RuntimeError):
    """Raised when the RunPod API returns an error or unparseable response."""


@dataclass(frozen=True)
class Pod:
    """A RunPod pod relevant to credit decisions.

    `cost_per_hr` is the published rate at query time; the actual hourly
    burn is approximately this if the pod is running.
    """

    id: str
    name: str
    cost_per_hr: float
    desired_status: str  # RUNNING / EXITED / etc.

    @property
    def is_running(self) -> bool:
        return self.desired_status.upper() == "RUNNING"


class RunPodClient:
    """Thin GraphQL client for the two queries RunPodBoss needs.

    Args:
        api_key: A RunPod API key (Account → Settings → API Keys).
        timeout: Per-request timeout in seconds.
        url: Override the GraphQL endpoint (for tests).
    """

    def __init__(
        self,
        api_key: str,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        url: str = RUNPOD_GRAPHQL_URL,
    ) -> None:
        if not api_key:
            raise ValueError("RunPod API key is required.")
        self._api_key = api_key
        self._timeout = timeout
        self._url = url

    def get_balance_usd(self) -> float:
        """Return the operator's remaining credit balance in USD."""
        query = "{ myself { clientBalance } }"
        data = self._post(query)
        try:
            return float(data["myself"]["clientBalance"])
        except (KeyError, TypeError, ValueError) as e:
            raise RunPodAPIError(f"Unexpected balance response shape: {data!r}") from e

    def list_pods(self) -> list[Pod]:
        """Return all pods on the account regardless of state.

        The caller can filter to running ones via `pod.is_running`; we
        return everything so an emergency-mode Claude agent can also see
        recently-stopped pods that may have stranded artifacts.
        """
        query = """
        {
          myself {
            pods {
              id
              name
              costPerHr
              desiredStatus
            }
          }
        }
        """
        data = self._post(query)
        try:
            raw = data["myself"]["pods"] or []
        except (KeyError, TypeError) as e:
            raise RunPodAPIError(f"Unexpected pods response shape: {data!r}") from e
        out: list[Pod] = []
        for p in raw:
            try:
                out.append(
                    Pod(
                        id=str(p["id"]),
                        name=str(p.get("name") or ""),
                        cost_per_hr=float(p.get("costPerHr") or 0.0),
                        desired_status=str(p.get("desiredStatus") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError) as e:
                raise RunPodAPIError(f"Unparseable pod row: {p!r}") from e
        return out

    def _post(self, query: str) -> dict[str, Any]:
        """Send a GraphQL query, return the `data` field, raise on errors."""
        body = json.dumps({"query": query}).encode("utf-8")
        req = urllib.request.Request(
            self._url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RunPodAPIError(f"HTTP {e.code} from RunPod: {e.reason}") from e
        except urllib.error.URLError as e:
            raise RunPodAPIError(f"Network error talking to RunPod: {e.reason}") from e
        except json.JSONDecodeError as e:
            raise RunPodAPIError(f"RunPod returned non-JSON: {e}") from e
        if payload.get("errors"):
            msg = "; ".join(err.get("message", "?") for err in payload["errors"])
            raise RunPodAPIError(f"RunPod GraphQL error: {msg}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise RunPodAPIError(f"RunPod response missing 'data': {payload!r}")
        return data
