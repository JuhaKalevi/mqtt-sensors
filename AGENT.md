# AGENT.md

This repo is the absolute minimum needed to get host stats onto an MQTT broker. Collectors today: RAPL CPU package, NVIDIA GPU, Chia farm size, Chia recompute time, Chia harvester time. They share one HA device per hostname. A new sensor is a small script that copies those. Keep it that small.

## Intent

Publish numbers to MQTT so Home Assistant can discover them. Not a framework, not a metrics stack, not a supervisor.

## Adding a sensor

1. New script at repo root. Import `mqtt_common`. Do not add packages.
2. `HOSTNAME = get_hostname()` and `DEVICE, _ = make_device(HOSTNAME)`. File: `{source}_{metric}.py`. unique_id and object: `{source}_{metric}_{hostname}`. HA `name` is a readable title that includes the metric (`Chia Recompute Server Processing Time`). Never omit the metric. Never use the raw binary name as the HA name. Availability: `sensor/{source}_{hostname}/availability`. Client id: `{source}-{hostname}`. Same HA device as the other collectors on that host.
3. One held-open source for the life of the process: sysfs fd, log fd, one subprocess that loops internally (`nvidia-smi --loop=1`, `journalctl -f`), or one HTTP/TLS connection. **Never** `Popen` per sample. **Never** open a new TCP connection per sample if you can hold one. Reopening a rotated log fd (inode change, once a day) is allowed; do not start `tail`.
4. `create_client(..., will_topic=availability)` then `loop_start()`. On connect: retained discovery JSON + `online`. On exit: `offline`, stop, disconnect.
5. ~1 s using `time.monotonic()` for `dt`. Retained state.
6. Power: W, `make_power_discovery`, `%.1f`.
7. Energy (if any): RAM only, `energy_wh += power * (dt / 3600.0)`, publish `energy_wh / 1000.0` as kWh `%.6f`, `make_energy_discovery` (`total_increasing`). It resets when the process starts. That is correct for HA. Do not write it to disk.
8. Optional matching `*.service`: only `ExecStart`, `WorkingDirectory`, `[Install] WantedBy=default.target`. Path stub `/root/mqtt-sensors`. All collectors run as root. Do not add `User=` / `Group=`. Chia paths: `Path(pwd.getpwuid(1000).pw_dir) / ".chia" / …` — never a username, never `/home/…`.

Other measurement types: `make_sensor_discovery(...)` with the HA unit / device_class / state_class. Do not extend `mqtt_common.py` for a one-off. Chia plots omit `device_class` (plain count). Chia sizes use `TiB` / `data_size`, netspace `EiB` / `data_size`, ETA `s` / `duration`. Recompute and harvester processing time are `s` / `duration`; publish full precision, set `suggested_display_precision` to 1 on that dict. Publish each sample; do not average. Do not publish fail or gpu flags; gaps in time are enough.

## Hard rules

- No comments.
- Minimal error handling. Assume RAPL, `nvidia-smi`, the Chia farmer on localhost:8559, full node on localhost:8555, farmer + full_node certs and `debug.log` under uid 1000's `.chia/mainnet`, `journalctl -u chia_recompute_server`, the broker, and `.env` work.
- No logging, retries, backoff, reconnect logic, tests, types, CLI flags, extra config, or dependencies beyond `paho-mqtt`.
- Do not add systemd hardening, `Restart=`, `[Unit]` keys, or healthchecks.
- Do not persist energy, add `last_reset`, MQTT TLS, or HA extras unless asked.
- Do not introduce a Sensor class. Similar scripts beat an abstraction.
- Leave existing comments and defensive parsing in the current scripts. Do not clean them up and do not copy them into new ones.

## Layout

- `mqtt_common.py` — dotenv, hostname, discovery, client + LWT
- `cpu_package_power.py` — RAPL package, fd held open, wrap via `(curr - prev) % max_energy_range_uj`
- `nvidia_gpu_power.py` — one `nvidia-smi --loop=1`, multi-GPU
- `chia_farm_size.py` — held HTTPS to farmer `get_harvesters_summary` and full node `get_blockchain_state`; plots + TiB + effective TiB + netspace EiB + ETA seconds `(space/effective)*18.75`; certs under uid 1000 home
- `chia_farm_size.service` — `/root/mqtt-sensors`
- `chia_recompute_server_processing_time.py` — one `journalctl -u chia_recompute_server -f`, each line → full-precision seconds, display 1 decimal
- `chia_recompute_server_processing_time.service` — `/root/mqtt-sensors`
- `chia_harvester_processing_time.py` — held `debug.log` fd under uid 1000 home, `Time: N s` on eligible-plots lines, reopen on inode change
- `chia_harvester_processing_time.service` — `/root/mqtt-sensors`
- `*.service` — path stubs under `/root/mqtt-sensors`
- `.env` — gitignored
- `screen.png` — HA device page (hostname device, CPU + Chia)
