# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""End-to-end integration: real urllib client against a real localhost HTTP server.

The other test files mock the network at various levels. This one spins up
`http.server.HTTPServer` on a random port, points a freshly-written config
at it, and runs `runpodboss check` like a user would. That exercises the
*actual* urllib code path — JSON encode, real socket, real HTTP response,
real parse — instead of trusting monkeypatched stubs.
"""

from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from runpodboss.cli import main as cli_main

# Canned responses keyed by the GraphQL query body's first non-whitespace
# substring. We use a substring match because the queries have inline
# formatting and the exact bytes can drift; matching the field name
# (`clientBalance`, `pods`) is enough.

CANNED_BALANCE_RESPONSE: dict[str, Any] = {
    "data": {"myself": {"clientBalance": 4.2345}},
}

CANNED_PODS_RESPONSE: dict[str, Any] = {
    "data": {
        "myself": {
            "pods": [
                {
                    "id": "pod-aaaa",
                    "name": "training-rig",
                    "costPerHr": 1.49,
                    "desiredStatus": "RUNNING",
                },
                {
                    "id": "pod-bbbb",
                    "name": "stopped-vm",
                    "costPerHr": 0.34,
                    "desiredStatus": "EXITED",
                },
            ]
        }
    },
}


class _FakeRunPodHandler(BaseHTTPRequestHandler):
    """Pretends to be the RunPod GraphQL endpoint.

    Routes based on whether the query string mentions `clientBalance` or
    `pods`. Authorization header is asserted on every request so the test
    catches any regression where the bearer token stops being sent.
    """

    # Class-level latch — tests inspect this after the server runs.
    received_auth_headers: list[str] = []

    def log_message(self, format, *args):  # noqa: A002 — match base API
        # Swallow the server's own access-log noise.
        pass

    def do_POST(self):
        self.received_auth_headers.append(self.headers.get("Authorization", ""))
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        try:
            query = json.loads(body).get("query", "")
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return
        if "clientBalance" in query:
            payload = CANNED_BALANCE_RESPONSE
        elif "pods" in query:
            payload = CANNED_PODS_RESPONSE
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"errors":[{"message":"unknown query"}]}')
            return
        body_bytes = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)


def _free_port() -> int:
    """Bind to an OS-assigned port and immediately close so the next
    bind() can claim it. There's an unavoidable TOCTOU window but it's
    near-zero risk on a developer box and CI runner."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def _fake_runpod_server():
    """Yield (url, handler-class) for a running fake RunPod server."""
    _FakeRunPodHandler.received_auth_headers = []
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _FakeRunPodHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/graphql", _FakeRunPodHandler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _write_config(tmp_path, runpod_url: str) -> str:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "api_key": "integration-test-key",
                "runpod_url": runpod_url,
                "thresholds": [
                    {"name": "warning", "below_usd": 10.0, "prompt": "p {balance}"},
                ],
                "state_file": str(tmp_path / "state.json"),
                "log_file": str(tmp_path / "boss.log"),
            }
        ),
        encoding="utf-8",
    )
    return str(cfg_path)


def test_check_command_prints_balance_and_pods_from_real_http(tmp_path, capsys):
    with _fake_runpod_server() as (url, handler):
        cfg_path = _write_config(tmp_path, url)
        rc = cli_main(["-c", cfg_path, "check"])

    assert rc == 0
    out = capsys.readouterr().out
    # Balance from the canned response.
    assert "balance: $4.2345" in out
    # Both pods rendered, with their names + costs.
    assert "pod-aaaa" in out
    assert "training-rig" in out
    assert "1.4900/hr" in out
    assert "pod-bbbb" in out
    assert "stopped-vm" in out
    # Two requests went over the wire (balance, then pods).
    assert len(handler.received_auth_headers) == 2
    # Every request carried the bearer token.
    for h in handler.received_auth_headers:
        assert h == "Bearer integration-test-key"


def test_check_command_reports_runpod_graphql_errors(tmp_path, capsys):
    """If the server responds with `errors`, the CLI surfaces the message
    cleanly and exits non-zero."""

    class _ErrorHandler(_FakeRunPodHandler):
        def do_POST(self):
            self.received_auth_headers.append(self.headers.get("Authorization", ""))
            body = json.dumps({"errors": [{"message": "Unauthorized"}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _ErrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg_path = _write_config(tmp_path, f"http://127.0.0.1:{port}/graphql")
        rc = cli_main(["-c", cfg_path, "check"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert rc == 2
    err = capsys.readouterr().err
    assert "Unauthorized" in err


def test_check_command_handles_no_pods(tmp_path, capsys):
    """RunPod returns an empty pod list on accounts with no active pods."""

    class _NoPodsHandler(_FakeRunPodHandler):
        def do_POST(self):
            self.received_auth_headers.append(self.headers.get("Authorization", ""))
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            query = json.loads(body).get("query", "")
            payload = (
                {"data": {"myself": {"clientBalance": 25.0}}}
                if "clientBalance" in query
                else {"data": {"myself": {"pods": []}}}
            )
            body_bytes = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _NoPodsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg_path = _write_config(tmp_path, f"http://127.0.0.1:{port}/graphql")
        rc = cli_main(["-c", cfg_path, "check"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert rc == 0
    out = capsys.readouterr().out
    assert "balance: $25.0000" in out
    assert "(none)" in out
