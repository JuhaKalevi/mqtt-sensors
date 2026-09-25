# CODE_OF_CONDUCT.md

How we treat this codebase. Not a community civility policy — [AGENT.md](AGENT.md) is the sensor contract, [SECURITY.md](SECURITY.md) is the supply-chain / root policy. This file is the attitude those files assume.

## Fail fast

A collector that cannot do its job should exit. Wrong or missing `XMRIG_TOKEN`, HA unreachable when spot is required, RAPL gone, broker down: crash. `Restart=on-failure` and `RestartSec=10` on the unit are the recovery path. Do not add retries, backoff, reconnect loops, or “degraded mode” so the process can limp along publishing nothing useful.

If a sensor is optional on a host, do not enable its unit. Enabling it means its prerequisites are present.

## Minimal surface

No comments in new code. No logging frameworks. No tests harness. No types. No CLI flags. No extra config beyond `.env` and the few env vars a sensor already documents. No new dependencies. Distro packages only — see `SECURITY.md`.

Diagnostic prints that name a missing MQTT input or a bad HA poll are fine when they prevent a wild goose chase; they are not a license to swallow errors and keep going.

## Assume the environment

Scripts assume their source works: XMRig HTTP with the token you set, retained MQTT from the helpers you run, `.env` loaded from `WorkingDirectory`, the broker reachable. Fix the host or the unit env; do not pad the script to tolerate a half-setup forever.

## Keep AGENT.md small

Operational detail and one-off sensor notes belong in `README.md` or a short commit message. Security reasoning belongs in `SECURITY.md`. Do not grow `AGENT.md` into a novel. Do not invent abstractions (`Sensor` classes, shared supervisors) to avoid repeating a small script. A generalisation may cross that line only if it is built so something else can easily leverage it later: a reusable building block in `mqtt_common.py` (like the switch and systemd unit helpers), not a merged collector. Each source still keeps its own script and unit.

## Pluggable service manager

Anything that reads or controls a service goes through `service_manager()` in `mqtt_common.py` and its three methods: `is_active(name)`, `start(name)`, `stop(name)`. systemd (`SystemdServiceManager`) is the only backend we ship. Someone on OpenRC, runit, s6 or anything else must be able to plug in their own backend there without touching a collector. We do not write those backends; we keep the door obvious. Collectors never call `systemctl`, read cgroups, or talk D-Bus themselves. No backend setting or plugin loader until there is a second backend: the one function is the switch.

## Pull requests

Match the style of the neighbor script. Prefer deleting lines. If a change needs a long apology in the description, it is probably the wrong change for this repo.

## Windows

`windows/` copies the Linux power collectors as reference and may assume LibreHardwareMonitor or `nvidia-smi` is already running. That exception does not apply to the Linux collectors. Still fail-fast: missing source exits. Do not vendor a driver.
