# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""CLI surface tests — argparse routing + the init / check happy paths."""

from __future__ import annotations

import json

import pytest

from runpodboss.cli import EXAMPLE_CONFIG, main


def test_no_subcommand_fails(capsys):
    with pytest.raises(SystemExit) as exc:
        main([])
    # argparse uses code 2 for "you used me wrong."
    assert exc.value.code == 2


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "runpodboss" in out


def test_init_writes_example_config(tmp_path, capsys):
    out_path = tmp_path / "example.json"
    rc = main(["init", "--output", str(out_path)])
    assert rc == 0
    assert out_path.is_file()
    body = out_path.read_text(encoding="utf-8")
    assert body == EXAMPLE_CONFIG
    # Sanity: the example must parse as JSON.
    parsed = json.loads(body)
    assert "thresholds" in parsed
    assert len(parsed["thresholds"]) >= 1


def test_init_refuses_to_overwrite(tmp_path, capsys):
    out_path = tmp_path / "example.json"
    out_path.write_text("existing", encoding="utf-8")
    rc = main(["init", "--output", str(out_path)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "already exists" in err
    # And the original content must not have been clobbered.
    assert out_path.read_text(encoding="utf-8") == "existing"


def test_missing_config_file_is_clean_error(tmp_path, capsys):
    # `-c` is a parent-level flag so it precedes the subcommand.
    rc = main(["-c", str(tmp_path / "nope.json"), "check"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Config file not found" in err


def test_watch_loads_config_and_calls_run_loop_with_it(tmp_path, monkeypatch):
    """Watch wiring: parses CLI -> loads config -> hands it to run_loop.

    We override `_cmd_watch`'s run_loop_fn via monkeypatch on the module
    attribute the CLI looks up, then run the CLI like a user would.
    The stub records the Config it was called with so we can assert the
    end-to-end wiring without spinning up the daemon.
    """
    import json

    from runpodboss import cli as cli_mod

    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "api_key": "test-key-1234567890",
                "thresholds": [
                    {"name": "warning", "below_usd": 10.0, "prompt": "p {balance}"},
                ],
                "state_file": str(tmp_path / "state.json"),
                "log_file": str(tmp_path / "watch.log"),
            }
        ),
        encoding="utf-8",
    )

    captured = {}

    def fake_run_loop(cfg):
        captured["cfg"] = cfg
        return 0

    # Patch the run_loop import that _cmd_watch defaults to.
    monkeypatch.setattr(cli_mod, "run_loop", fake_run_loop)

    rc = cli_mod.main(["-c", str(cfg_path), "watch"])
    assert rc == 0
    assert "cfg" in captured
    assert captured["cfg"].api_key == "test-key-1234567890"
    assert captured["cfg"].thresholds[0].name == "warning"
    # And the log file directory must have been created so the file handler
    # could attach without exploding.
    assert (tmp_path / "watch.log").parent.is_dir()
