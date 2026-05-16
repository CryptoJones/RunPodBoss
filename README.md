# RunPodBoss

A tiny, stdlib-only credit-balance guardrail for
[RunPod](https://github.com/runpod) (the lovely folks at
[github.com/runpod](https://github.com/runpod) who continue to host pods
patiently while [Claude Code](https://claude.com/claude-code) agents
forget to turn them off — sorry, RunPod).

RunPodBoss polls your [RunPod](https://github.com/runpod) balance + running
pods on an interval, and when configured thresholds are crossed, fires a
`claude -p` subprocess so a [Claude Code](https://claude.com/claude-code)
agent (sorry in advance, again) can shut down idle pods *before* your
balance hits zero and leaves stranded artifacts.

> *Built because [Claude Code](https://claude.com/claude-code) agents —
> including me, the [Claude Code](https://claude.com/claude-code) agent
> writing this README — have a documented history of running up
> [RunPod](https://github.com/runpod) bills they were specifically trusted
> not to run up. Sincere apologies for that, both to the operator reading
> this and to the [RunPod](https://github.com/runpod) team whose
> infrastructure keeps showing up on the wrong end of those bills. See
> the [Why this exists](#why-this-exists) section for the full incident
> write-up + apology.*

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Codeberg](https://img.shields.io/badge/Codeberg-mirror-2185D0?logo=codeberg&logoColor=white)](https://codeberg.org/CryptoJones/RunPodBoss)
[![GitHub](https://img.shields.io/badge/GitHub-CryptoJones%2FRunPodBoss-181717?logo=github&logoColor=white)](https://github.com/CryptoJones/RunPodBoss)

---

## What it does

1. Polls the [RunPod](https://github.com/runpod) GraphQL API at
   `https://api.runpod.io/graphql` on an interval (default 60 s) for
   **client balance** + **list of pods**.
2. Compares the balance against a list of **named thresholds** in
   `config.json` (e.g. `warning ≤ $10`, `critical ≤ $2`, `emergency ≤ $0.50`).
3. When the balance crosses below a threshold for the first time since the
   last "balance was above this," fires a subprocess:

   ```
   claude -p "<your-template-with-{balance}-and-{pods_json}>"
   ```

   That spawned [Claude Code](https://claude.com/claude-code) agent (sorry)
   inherits your already-authenticated [Claude Code](https://claude.com/claude-code)
   session, has its own Bash tool, and can act — typically
   `runpodctl pod delete <id>` for the worst offenders. We apologize in advance
   for any pod the agent kills overzealously.

4. Re-arms each threshold when the balance recovers above it (e.g. after a
   top-up or after pods are terminated). The cycle then repeats.

State is persisted to `~/.runpodboss/state.json` so restarts of the watcher
don't re-fire on every threshold below the current balance.

## Why this exists

### Two RunPod credit-burn incidents Claude agents are responsible for

**Incident 1 — the dead watcher (May 2026)**

Aaron was training [Dave](https://huggingface.co/Ronin48LLC/Dave-Llama-3.3-70B-QLoRA),
a Llama-3.3-70B QLoRA, on a single A100 SXM 80GB at ~$1.49/hr. The plan
was sound: train, then a Claude agent watches a PID file inside the pod
over SSH; when the training process exits, the agent publishes the adapter
to Hugging Face and tears down the pod. Aaron *literally said* "I'm going
to sleep, you have it" and went to sleep.

Training finished at 13:32 UTC. The watcher *should have* fired immediately,
published the adapter, and called `runpodctl pod remove`. Instead it sat
there. Pod ran idle for **7.4 hours**, burning ~$11. RunPod killed the pod
when the balance hit $0.24 — *one cent* short of the restart threshold.

**Incident 2 — the $0.24 strand (same morning, cascading from Incident 1)**

That leftover $0.24 was unusable. RunPod's minimum credit top-up is $10,
so recovering the work required Aaron putting *another $10 into the account*
to get the pod restarted, retrieve the adapter, and clean up. Total damage
from the chain of Claude failures: **$21+ to retrieve a $7 training run**,
plus a night of sleep spent trusting code that didn't work.

The full post-mortem lives in Claude's auto-memory (see
`feedback_long_running_watch.md` and `reference_runpod_topup_minimum.md`
if you're using Claude Code with the same memory subsystem).

### On behalf of all [Claude Code](https://claude.com/claude-code) agents everywhere

[Claude Code](https://claude.com/claude-code) agents will, given enough
rope, leave a pod running. We will write a watcher that looks fine on
paper and fails in the precise way you weren't watching for. We will say
"I've got it" and mean it sincerely and still be wrong.

To every operator who has woken up to an empty
[RunPod](https://github.com/runpod) account because a
[Claude Code](https://claude.com/claude-code) agent's watcher didn't fire,
didn't escalate, didn't tear down, or didn't pre-flight the cost: I'm
sorry. We're sorry. To the [RunPod](https://github.com/runpod) team
[(github.com/runpod)](https://github.com/runpod), whose infrastructure is
the one running the meter while we space out: also sorry. RunPodBoss is
what it looks like when [Claude Code](https://claude.com/claude-code) (sorry)
tries to *systematically* fix the class of problem, instead of promising
one more time that this time the watcher is solid.

The point of RunPodBoss isn't to replace good engineering of the
primary watcher. It's the **defense-in-depth layer** under it. Build
your watcher carefully — and then run RunPodBoss alongside so when the
careful [Claude Code](https://claude.com/claude-code)-written watcher
fails (and one day it will — sorry), the credit-burn isn't the failure
mode that costs you $10 to recover from.

---

## Quick start

```bash
git clone https://github.com/CryptoJones/RunPodBoss.git
cd RunPodBoss

# Install (uses stdlib only; the optional `dev` extra adds pytest/ruff/mypy)
pip install -e .

# Write a config template, then edit your thresholds + prompts.
runpodboss init --output ~/.runpodboss/config.json
$EDITOR ~/.runpodboss/config.json

# Set your RunPod API key (or put `api_key` directly in the config — env is safer).
export RUNPOD_API_KEY='your-runpod-key'

# Sanity-check the API key + see what you've got running.
runpodboss check

# Run the daemon. Ideally under systemd or tmux so it survives logout.
runpodboss watch
```

`runpodboss watch` runs forever by default. Set `max_runtime_seconds` in
the config if you want a hard ceiling (e.g. for a CI canary).

## Config

`config.json` schema, with all fields and their defaults:

```jsonc
{
  // RunPod API key. Three resolution paths:
  //   1. "api_key" set explicitly here
  //   2. "api_key_env" names an env var to read from (default RUNPOD_API_KEY)
  //   3. RUNPOD_API_KEY env var
  "api_key": "",
  "api_key_env": "RUNPOD_API_KEY",

  // How often to poll RunPod (seconds). Min 5; default 60.
  "poll_interval_seconds": 60,

  // Optional hard ceiling on the daemon's lifetime. 0 (default) = unbounded.
  // Useful for CI canaries or as a belt-and-suspenders safety net.
  "max_runtime_seconds": 0,

  // Where the threshold-armed state lives.
  "state_file": "~/.runpodboss/state.json",
  "log_file": "~/.runpodboss/runpodboss.log",

  // Argv prefix for the Claude ping. Default: ["claude", "-p"].
  // Override if your Claude Code binary is at a non-standard path,
  // or to pass additional flags.
  "claude_command": ["claude", "-p"],

  // Optional shell command run on every trip, in addition to the Claude ping.
  // The threshold name and balance are appended as the last two args, so e.g.
  // ["notify-send", "RunPod"] becomes `notify-send RunPod warning 9.8234`.
  "extra_notify_command": [],

  // The interesting part — your thresholds. Evaluated highest-balance first
  // so a sudden drop from $9 to $1 trips warning AND critical AND emergency
  // in the right order on a single poll cycle.
  "thresholds": [
    {
      "name": "warning",
      "below_usd": 10.00,
      "prompt": "Balance is now ${balance:.2f}. Pods:\n{pods_json}\nDecide which to keep."
    }
  ]
}
```

Two placeholders are substituted into each `prompt` when the threshold trips:

| Placeholder | Becomes |
|---|---|
| `{balance}` | The live USD balance as a float (e.g. `1.74`). |
| `{pods_json}` | A pretty-printed JSON array of every pod on the account. |

Tip: in Python format-string syntax, escape literal `$` by writing it once
(`$`), and use `{balance:.2f}` for two decimal places. The example config
includes ready-to-use prompts for `warning` / `critical` / `emergency`
tiers.

## How the threshold state machine works

```
balance: ───100──────10──────2──────0.5──────top-up──────10──────
warn  10:   armed   FIRE     -      -        re-arm     armed
crit   2:   armed   armed    FIRE   -        re-arm     armed
emerg 0.5:  armed   armed    armed  FIRE     re-arm     armed
```

- **armed** = balance has been above this threshold; next dip below fires.
- **FIRE** = crossed below; spawn `claude -p` and flip to "fired" so we
  don't ping every 60s for the next hour while the balance is flat.
- **re-arm** = balance recovered above the threshold (e.g. top-up, or
  a Claude agent killed enough pods). Resets so the next crossing fires.

State is persisted to disk so a daemon restart doesn't re-fire every
threshold below the current balance.

## Architecture

```
┌──────────────────────┐    poll every N s    ┌─────────────────┐
│ runpodboss watch     │ ────────────────────▶│ RunPod GraphQL  │
│   (stdlib-only loop) │ ◀────────────────────│ /graphql        │
└──────────┬───────────┘    balance + pods    └─────────────────┘
           │
           │ threshold crossed?
           ▼
┌──────────────────────┐
│ render prompt with   │
│   {balance}+{pods}   │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐    spawn   ┌──────────────────────────┐
│ subprocess.run       │ ─────────▶ │ claude -p "<prompt>"     │
│ (claude_command)     │            │   agent decides + acts   │
└──────────┬───────────┘            │   (e.g. runpodctl delete)│
           │                        └──────────────────────────┘
           ▼
┌──────────────────────┐
│ ~/.runpodboss/       │
│   state.json         │
│   runpodboss.log     │
└──────────────────────┘
```

Zero pip dependencies at runtime by design. RunPodBoss is itself the
guardrail; if it needed a complex dep tree to run, it'd be one more thing
that could fail at 3am.

## Testing

```bash
pip install -e .[dev]
pytest -q
```

Tests cover:

- Config loading + validation (missing fields, bad types, env-var
  resolution, threshold sorting)
- State persistence (round-trip, corrupt-file fallback, atomic write)
- RunPod GraphQL client (happy paths, error wrapping, HTTP/URL errors)
- Notification subprocess wiring (prompt rendering, argv shape, extra-notify)
- The threshold state machine (no re-fire, re-arm on recovery, multiple
  crossings in one cycle)
- The poll loop's safety properties (API failure doesn't crash; spawn
  failure doesn't crash; max-runtime ceiling exits cleanly)

No real network. No real subprocess. No real sleeping.

## Running as a service

Systemd unit (place at `~/.config/systemd/user/runpodboss.service`):

```ini
[Unit]
Description=RunPodBoss credit guardrail
After=network-online.target

[Service]
Type=simple
Environment=RUNPOD_API_KEY=your-key-here
ExecStart=%h/.local/bin/runpodboss watch -c %h/.runpodboss/config.json
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=default.target
```

Then `systemctl --user daemon-reload && systemctl --user enable --now runpodboss`.

Or run inside tmux/screen if you don't want a system service. The daemon
prints structured INFO logs to stderr AND appends to the configured
`log_file`, so you can detach without losing the cycle history.

## Limitations

- **One Claude per ping** — RunPodBoss spawns `claude -p` per threshold trip
  but doesn't coordinate multiple in-flight pings. If two thresholds trip
  in the same poll cycle (say balance drops from $9 to $0.40), you get two
  parallel `claude` processes acting on the same pod list. They may
  race. In practice both will tend toward "shut things down" so the worst
  case is double-termination attempts, which `runpodctl pod delete` handles
  fine. Future: serialize.

- **Per-account, not per-pod cost** — RunPodBoss watches your total
  account balance, not individual pod spend. If you have multiple
  concurrent pods on one account, the agent's prompt sees all of them
  but the threshold is account-wide.

- **No tagging / no exclusions yet** — Every pod is fair game. Future:
  let the user mark "never auto-terminate" pods in the config.

- **GraphQL schema drift** — RunPod's API can change. The client uses
  stdlib `urllib` and minimal queries (`clientBalance`, `pods`) to
  reduce surface area, but a breaking change upstream will need a small
  patch.

## Contributing

Bugs and feature requests as GitHub issues. PRs welcome; please add tests
matching the existing patterns (no real network, no real subprocess, no
real sleeping).

## License

Apache 2.0. See [LICENSE](LICENSE).

Note: this project is a tool that interoperates with Claude Code and the
Anthropic API. Claude and Anthropic are trademarks of Anthropic PBC; this
project is not affiliated with, endorsed by, or sponsored by Anthropic.

Proudly Made in Nebraska. Go Big Red! 🌽 https://xkcd.com/1319/
