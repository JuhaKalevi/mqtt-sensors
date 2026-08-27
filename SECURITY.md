# SECURITY.md

This repo runs as root. Supply-chain risk is the threat model. More notes belong here as they come up.

## Distro packages only

There is no `requirements.txt`, no `Pipfile`, no `pyproject.toml` dependencies, no `package-lock.json`, and there will not be. We do not pip-install. We do not vendor wheels. We do not fetch libraries from PyPI, GitHub releases, or any other third-party index.

Python and `paho-mqtt` come from the distribution's standard package list (`python3`, `python3-paho-mqtt` on Debian/Ubuntu). Third-party sources cannot be considered, even if they look convenient. If a distro does not ship it, we do not use it — we write the few lines ourselves or we drop the idea.

Stdlib, a sysfs fd, a local log, and binaries already on the machine (`nvidia-smi`, `journalctl`, the Chia farmer) beat a package. `paho-mqtt` is the existing exception and it still arrives via the distro.

If a sensor's logic depends on something that could be a supply-chain risk, and a small custom solution would be safer, consider that rework. Not necessarily immediately. That is the spirit.

## Root

Everything here is meant to run as root from `/root/mqtt-sensors`. If you would not want a sensor doing its job as root, it does not belong in this project. Do not add `User=` as a substitute for shrinking the dependency surface.

Chia files live under uid 1000's home via `chia_root()` (`pwd.getpwuid(1000).pw_dir`). That is a path lookup, not a privilege drop. Never `Path.home()` — as root that is `/root`.

## What this file is for

Add concrete risks, rejected approaches, and rework notes here as they appear. Keep AGENT.md and README.md short; put the security reasoning in this file.
