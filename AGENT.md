# AGENT.md

This repo is the absolute minimum needed to get host stats onto an MQTT broker. Collectors today: RAPL CPU package, NVIDIA GPU, Chia farm size. They share one HA device per hostname. A new sensor is a small script that copies those. Keep it that small.

## Intent

Publish numbers to MQTT so Home Assistant can discover them. Not a framework, not a metrics stack, not a supervisor.

## Adding a sensor

1. New script at repo root. Import `mqtt_common`. Do not add packages.
2. `HOSTNAME = get_hostname()` and `DEVICE, _ = make_device(HOSTNAME)`. Put the hostname in every unique_id and client_id. Same HA device as the other collectors on that host.
3. One held-open source for the life of the process: sysfs fd, one subprocess that loops internally, or one HTTP/TLS connection. **Never** `Popen` per sample. **Never** open a new TCP connection per sample if you can hold one.
4. `create_client(..., will_topic=availability)` then `loop_start()`. On connect: retained discovery JSON + `online`. On exit: `offline`, stop, disconnect.
5. ~1 s using `time.monotonic()` for `dt`. Retained state.
6. Power: W, `make_power_discovery`, `%.1f`.
7. Energy (if any): RAM only, `energy_wh += power * (dt / 3600.0)`, publish `energy_wh / 1000.0` as kWh `%.6f`, `make_energy_discovery` (`total_increasing`). It resets when the process starts. That is correct for HA. Do not write it to disk.
8. Optional matching `*.service`: only `ExecStart`, `WorkingDirectory`, `[Install] WantedBy=default.target`. Leave the path as a stub.

Other measurement types: `make_sensor_discovery(...)` with the HA unit / device_class / state_class. Do not extend `mqtt_common.py` for a one-off. Chia plots omit `device_class` (plain count). Chia sizes use `TiB` / `data_size`.

## Hard rules

- No comments.
- Minimal error handling. Assume RAPL, `nvidia-smi`, the Chia farmer on localhost:8559, farmer certs under `~/.chia/mainnet`, the broker, and `.env` work.
- No logging, retries, backoff, reconnect logic, tests, types, CLI flags, extra config, or dependencies beyond `paho-mqtt`.
- Do not add systemd hardening, `Restart=`, `[Unit]` keys, or healthchecks.
- Do not persist energy, add `last_reset`, MQTT TLS, or HA extras unless asked.
- Do not introduce a Sensor class. Similar scripts beat an abstraction.
- Leave existing comments and defensive parsing in the current scripts. Do not clean them up and do not copy them into new ones.

## Layout

- `mqtt_common.py` — dotenv, hostname, discovery, client + LWT
- `cpu_package_power.py` — RAPL package, fd held open, wrap via `(curr - prev) % max_energy_range_uj`
- `nvidia_gpu_power.py` — one `nvidia-smi --loop=1`, multi-GPU
- `chia_farm_size.py` — one held HTTPS connection to farmer `get_harvesters_summary`, plots + TiB + effective TiB
- `*.service` — path stubs
- `.env` — gitignored
- `screen.png` — HA device page (hostname device, CPU + Chia)
