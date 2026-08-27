# AGENT.md

This repo is the absolute minimum needed to get host stats onto an MQTT broker. Collectors today: RAPL CPU package, NVIDIA GPU, Chia farm size, Chia recompute time. They share one HA device per hostname. A new sensor is a small script that copies those. Keep it that small.

## Intent

Publish numbers to MQTT so Home Assistant can discover them. Not a framework, not a metrics stack, not a supervisor.

## Adding a sensor

1. New script at repo root. Import `mqtt_common`. Do not add packages.
2. `HOSTNAME = get_hostname()` and `DEVICE, _ = make_device(HOSTNAME)`. Put the hostname in every unique_id and client_id. Same HA device as the other collectors on that host.
3. One held-open source for the life of the process: sysfs fd, one subprocess that loops internally (`nvidia-smi --loop=1`, `journalctl -f`), or one HTTP/TLS connection. **Never** `Popen` per sample. **Never** open a new TCP connection per sample if you can hold one.
4. `create_client(..., will_topic=availability)` then `loop_start()`. On connect: retained discovery JSON + `online`. On exit: `offline`, stop, disconnect.
5. ~1 s using `time.monotonic()` for `dt`. Retained state.
6. Power: W, `make_power_discovery`, `%.1f`.
7. Energy (if any): RAM only, `energy_wh += power * (dt / 3600.0)`, publish `energy_wh / 1000.0` as kWh `%.6f`, `make_energy_discovery` (`total_increasing`). It resets when the process starts. That is correct for HA. Do not write it to disk.
8. Optional matching `*.service`: only `ExecStart`, `WorkingDirectory`, `[Install] WantedBy=default.target`. Root collectors stub `/root/mqtt-sensors` (CPU, GPU, recompute). Chia farm uses `%h/mqtt-sensors` (farm user home). Do not add `User=` or other unit keys.

Other measurement types: `make_sensor_discovery(...)` with the HA unit / device_class / state_class. Do not extend `mqtt_common.py` for a one-off. Chia plots omit `device_class` (plain count). Chia sizes use `TiB` / `data_size`, netspace `EiB` / `data_size`, ETA `s` / `duration`. Recompute time is `s` / `duration` at 1 decimal (`ms/1000`); fail is 0/1 with no device_class. Publish each journal line; do not average over the 10 s burst.

## Hard rules

- No comments.
- Minimal error handling. Assume RAPL, `nvidia-smi`, the Chia farmer on localhost:8559, full node on localhost:8555, farmer + full_node certs under `~/.chia/mainnet`, `journalctl -u chia_recompute_server`, the broker, and `.env` work.
- No logging, retries, backoff, reconnect logic, tests, types, CLI flags, extra config, or dependencies beyond `paho-mqtt`.
- Do not add systemd hardening, `Restart=`, `[Unit]` keys, or healthchecks.
- Do not persist energy, add `last_reset`, MQTT TLS, or HA extras unless asked.
- Do not introduce a Sensor class. Similar scripts beat an abstraction.
- Leave existing comments and defensive parsing in the current scripts. Do not clean them up and do not copy them into new ones.

## Layout

- `mqtt_common.py` — dotenv, hostname, discovery, client + LWT
- `cpu_package_power.py` — RAPL package, fd held open, wrap via `(curr - prev) % max_energy_range_uj`
- `nvidia_gpu_power.py` — one `nvidia-smi --loop=1`, multi-GPU
- `chia_farm_size.py` — held HTTPS to farmer `get_harvesters_summary` and full node `get_blockchain_state`; plots + TiB + effective TiB + netspace EiB + ETA seconds `(space/effective)*18.75`
- `chia_farm_size.service` — `%h/mqtt-sensors` (not root)
- `chia_recompute_server.py` — one `journalctl -u chia_recompute_server -f`, each line → time s (1 decimal) + fail 0/1, no averaging
- `chia_recompute_server.service` — `/root/mqtt-sensors`
- `*.service` — CPU/GPU path stubs under `/root/mqtt-sensors`
- `.env` — gitignored
- `screen.png` — HA device page (hostname device, CPU + Chia)
