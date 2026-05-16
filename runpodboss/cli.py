# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Aaron K. Clark
"""CLI entry point. Three subcommands:

  runpodboss check               One-shot poll: print balance + pods, exit.
  runpodboss watch               Long-running monitor (the daemon mode).
  runpodboss init                Write a config.example.json skeleton.

`watch` is the headline mode — it does the threshold evaluation +
Claude-ping logic. `check` is for sanity-testing the API key. `init`
is the friction-free first-run path.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__
from .config import DEFAULT_CONFIG_PATH, load_config
from .monitor import run_loop
from .runpod_api import RunPodAPIError, RunPodClient

# Long single-line prompts are intentional — JSON doesn't support line
# continuations and Claude's prompt template can't have raw newlines.
# fmt: off
# ruff: noqa: E501
EXAMPLE_CONFIG = """{
  "api_key_env": "RUNPOD_API_KEY",
  "poll_interval_seconds": 60,
  "max_runtime_seconds": 0,
  "state_file": "~/.runpodboss/state.json",
  "log_file": "~/.runpodboss/runpodboss.log",
  "claude_command": ["claude", "-p"],
  "extra_notify_command": [],
  "thresholds": [
    {
      "name": "warning",
      "below_usd": 10.00,
      "prompt": "RunPod balance is now $${balance:.2f}, below the $10 warning threshold. Running pods:\\n{pods_json}\\n\\nFor each pod, decide: keep running, or terminate? If terminating, run `runpodctl pod delete <id>`. Be conservative \\u2014 only terminate pods clearly past their useful life. Reply with a one-line summary of what you did."
    },
    {
      "name": "critical",
      "below_usd": 2.00,
      "prompt": "RunPod balance CRITICAL: $${balance:.2f}, below the $2 threshold. Running pods:\\n{pods_json}\\n\\nTerminate any pod that isn't actively producing artifacts. Use `runpodctl pod delete <id>` for each. Aggression > caution at this level."
    },
    {
      "name": "emergency",
      "below_usd": 0.50,
      "prompt": "RunPod EMERGENCY: balance $${balance:.2f}. RunPod kills pods around the $0.10 mark, leaving stranded artifacts that cost $10 to recover. Terminate ALL pods immediately:\\n{pods_json}\\nRun `runpodctl pod delete <id>` on every single one. Don't ask, don't pause."
    }
  ]
}
"""
# fmt: on


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="runpodboss",
        description="Background RunPod credit monitor — pings a Claude agent when thresholds trip.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "-c",
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"Config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log DEBUG instead of INFO.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("watch", help="Long-running monitor (the daemon).")
    sub.add_parser("check", help="One-shot poll: print balance + pods, then exit.")
    init_p = sub.add_parser("init", help="Write a config.example.json template.")
    init_p.add_argument(
        "--output",
        default="config.example.json",
        help="Where to write the example config (default: ./config.example.json).",
    )
    return p


def _cmd_check(cfg_path: str) -> int:
    cfg = load_config(cfg_path)
    client = RunPodClient(cfg.api_key)
    try:
        balance = client.get_balance_usd()
        pods = client.list_pods()
    except RunPodAPIError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"balance: ${balance:.4f}")
    if not pods:
        print("pods:    (none)")
    else:
        print("pods:")
        for p in pods:
            print(f"  {p.id}  {p.name!r}  ${p.cost_per_hr:.4f}/hr  status={p.desired_status}")
    return 0


def _cmd_watch(cfg_path: str) -> int:
    cfg = load_config(cfg_path)
    # Route logs to both stderr and the configured log file.
    cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(cfg.log_file)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(file_handler)
    return run_loop(cfg)


def _cmd_init(output: str) -> int:
    path = Path(output).expanduser()
    if path.exists():
        print(f"error: {path} already exists; refusing to overwrite.", file=sys.stderr)
        return 2
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    print(f"wrote {path}")
    print("Next: copy/edit, then `runpodboss watch -c <your-config.json>`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        if args.command == "check":
            return _cmd_check(args.config)
        if args.command == "watch":
            return _cmd_watch(args.config)
        if args.command == "init":
            return _cmd_init(args.output)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130

    # Should be unreachable — argparse's required=True covers the no-cmd case.
    print(f"unknown command: {args.command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
