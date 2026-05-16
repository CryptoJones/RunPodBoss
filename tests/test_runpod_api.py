# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""Tests for the RunPod GraphQL client.

The client uses urllib so the tests substitute a fake `urlopen` via
monkeypatch rather than spinning up an HTTP server.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from runpodboss.runpod_api import RUNPOD_GRAPHQL_URL, Pod, RunPodAPIError, RunPodClient


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return self._body


def _patch_urlopen(monkeypatch, response_payloads: list[dict]):
    """Queue up responses in order; raise IndexError if more calls happen."""
    calls = []
    iterator = iter(response_payloads)

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, req.method, req.data, dict(req.headers)))
        return _FakeResponse(next(iterator))

    monkeypatch.setattr("runpodboss.runpod_api.urllib.request.urlopen", fake_urlopen)
    return calls


def test_construct_requires_api_key():
    with pytest.raises(ValueError):
        RunPodClient("")


def test_get_balance_happy_path(monkeypatch):
    calls = _patch_urlopen(monkeypatch, [{"data": {"myself": {"clientBalance": 12.3456}}}])
    client = RunPodClient("k")
    assert client.get_balance_usd() == pytest.approx(12.3456)
    assert calls[0][0] == RUNPOD_GRAPHQL_URL
    assert calls[0][1] == "POST"
    # Authorization header is case-insensitive on urllib; the keys come through
    # capitalised, but the value must be a Bearer token with our key.
    headers = {k.lower(): v for k, v in calls[0][3].items()}
    assert headers["authorization"] == "Bearer k"


def test_get_balance_raises_on_bad_shape(monkeypatch):
    _patch_urlopen(monkeypatch, [{"data": {"myself": {}}}])  # no clientBalance
    client = RunPodClient("k")
    with pytest.raises(RunPodAPIError):
        client.get_balance_usd()


def test_get_balance_raises_on_graphql_errors(monkeypatch):
    _patch_urlopen(monkeypatch, [{"errors": [{"message": "Unauthorized"}]}])
    client = RunPodClient("k")
    with pytest.raises(RunPodAPIError, match="Unauthorized"):
        client.get_balance_usd()


def test_list_pods_happy_path(monkeypatch):
    _patch_urlopen(
        monkeypatch,
        [
            {
                "data": {
                    "myself": {
                        "pods": [
                            {
                                "id": "abc",
                                "name": "training",
                                "costPerHr": 1.49,
                                "desiredStatus": "RUNNING",
                            },
                            {
                                "id": "def",
                                "name": "idle",
                                "costPerHr": 0.34,
                                "desiredStatus": "EXITED",
                            },
                        ]
                    }
                }
            }
        ],
    )
    pods = RunPodClient("k").list_pods()
    assert pods == [
        Pod(id="abc", name="training", cost_per_hr=1.49, desired_status="RUNNING"),
        Pod(id="def", name="idle", cost_per_hr=0.34, desired_status="EXITED"),
    ]
    assert pods[0].is_running is True
    assert pods[1].is_running is False


def test_list_pods_empty_returns_empty_list(monkeypatch):
    _patch_urlopen(monkeypatch, [{"data": {"myself": {"pods": []}}}])
    assert RunPodClient("k").list_pods() == []


def test_list_pods_null_returns_empty_list(monkeypatch):
    # RunPod returns null for accounts with no pods on certain plans.
    _patch_urlopen(monkeypatch, [{"data": {"myself": {"pods": None}}}])
    assert RunPodClient("k").list_pods() == []


def test_http_error_wrapped_in_runpod_api_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, io.BytesIO(b""))

    monkeypatch.setattr("runpodboss.runpod_api.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RunPodAPIError, match="502"):
        RunPodClient("k").get_balance_usd()


def test_network_error_wrapped_in_runpod_api_error(monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    monkeypatch.setattr("runpodboss.runpod_api.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(RunPodAPIError, match="Network error"):
        RunPodClient("k").get_balance_usd()
