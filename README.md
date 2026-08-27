# mqtt-sensors

Long-lived processes that publish host stats to MQTT with Home Assistant discovery. Absolute minimum. One process per collector, one source held open for the life of that process. Everything here runs as root. If you would not want a sensor doing its job as root, it does not belong in this project.

Every collector on a host shares one HA device (`linux_host_<hostname>`), shown as the hostname. This is that device on `M710q`: RAPL package power/energy plus Chia farm plots, on-disk size, effective size, estimated netspace, and ETA to win.

![Home Assistant](screen.png)

## Collectors

| script | source (held open) | objects |
|---|---|---|
| `cpu_package_power.py` | `/sys/class/powercap/intel-rapl:*/energy_uj` (first `package*`) | `cpu_package_power_<host>` + `_energy` |
| `nvidia_gpu_power.py` | one `nvidia-smi --query-gpu=index,name,power.draw --loop=1` | `gpuN_power_<host>`, `gpuN_energy_<host>` per GPU |
| `chia_farm_size.py` | held TLS to farmer `:8559` `get_harvesters_summary` and full node `:8555` `get_blockchain_state` | `chia_plots_<host>`, `chia_farm_size_<host>`, `chia_farm_effective_<host>`, `chia_netspace_<host>`, `chia_eta_<host>` |
| `chia_recompute_server_processing_time.py` | one `journalctl -u chia_recompute_server -f -n 0 -o cat` | `chia_recompute_server_processing_time_<host>` |
| `chia_harvester_processing_time.py` | held fd on `~/.chia/mainnet/log/debug.log` (reopen on inode change) | `chia_harvester_processing_time_<host>` |

Topics under `$MQTT_PREFIX` (default `homeassistant`): `sensor/<object>/{config,state,availability}`. GPU availability is shared: `sensor/gpu_power_<host>/availability`. Chia farm: `sensor/chia_farm_<host>/availability`. Recompute: `sensor/chia_recompute_server_<host>/availability`. Harvester: `sensor/chia_harvester_<host>/availability`.

Power is W (`measurement`). Energy is kWh (`total_increasing`), integrated in RAM from 1 s samples, reset when the process starts. Chia sizes are TiB, netspace is EiB (`data_size`). Plots is a count. ETA to win is seconds (`duration`): `(netspace / effective) * 18.75`. Recompute and harvester publish full-precision seconds; HA displays 1 decimal via `suggested_display_precision`. Recompute work comes in 10 s bursts; times swinging from ~0.2 s to ~5 s is normal, do not average it away. Harvester time is the `Time: N s` on `plots were eligible for farming` lines; daily log rotate reopens the fd (no new process). GPU script is a no-op on machines without `nvidia-smi`. Chia farm needs the farmer, a local full node, and `~/.chia/mainnet` farmer + full_node certs. Harvester needs uid 1000's `~/.chia/mainnet/log/debug.log`. Recompute reads the `chia_recompute_server` journal.

## Naming

unique_id, MQTT object, and files: `{source}_{metric}` (+ `_{hostname}` on unique_id / object)

- `source` is the program or collector (`cpu_package`, `gpu0`, `chia_farm`, `chia_recompute_server`, `chia_harvester`)
- `metric` is what is measured (`power`, `energy`, `size`, `processing_time`, `plots`, `netspace`, `eta`)
- script `{source}_{metric}.py`, unit `{source}_{metric}.service`
- unique_id `{source}_{metric}_{hostname}`
- `hostname` is always last on unique_id

HA `name` is a readable title that includes the metric (`Chia Recompute Server Processing Time`, `Chia Farm Size`). Never drop the metric. unique_id stays snake_case; do not use the raw binary name as the HA name.

Availability is collector-level, no metric: `sensor/{source}_{hostname}/availability`. Client id: `{source}-{hostname}`. Device is always `linux_host_{hostname}`.

Publish full precision in state. If the UI should look coarser, set `suggested_display_precision` on that discovery dict only — do not round the payload.

## Adding a sensor

Copy an existing script. Do not add a framework.

1. New script in the repo root. Import from `mqtt_common` only.
2. `get_hostname()` + `make_device()` so unique_ids and the HA device include the host. Follow **Naming**: files `{source}_{metric}.py`, unique_id `{source}_{metric}_{hostname}`.
3. Hold one source open: a sysfs fd, a log fd, one subprocess with a native loop (`nvidia-smi --loop=1`, `journalctl -f`), or one HTTP/TLS connection. Never spawn per sample. Reopening a rotated log fd once a day is allowed.
4. Connect with LWT on an availability topic. On connect: retained discovery + `online`. On exit: `offline`.
5. ~1 s cadence. Publish retained state. Power is `%.1f` W. If you also publish energy, integrate in RAM (`power * dt / 3600` → kWh) with `state_class=total_increasing`. HA expects that counter to start at 0 when the process starts; do not persist it.
6. Optional `foo.service` stub: `ExecStart`, `WorkingDirectory`, `WantedBy=default.target`. Path is `/root/mqtt-sensors`. All collectors run as root. Nothing else.

Reuse `make_power_discovery` / `make_energy_discovery`, or `make_sensor_discovery` for other units. Do not grow `mqtt_common.py` unless several sensors need the same helper.

### Do not

- Comments
- Extra error handling, logging, retries, reconnect wrappers, fake data
- CLI flags, extra config files, new dependencies, tests, types, Docker, CI
- A Sensor base class
- systemd `Restart=`, `[Unit]` filler, hardening
- Dropping root. If the work should not run as root, it is the wrong repo.

Assume RAPL, `nvidia-smi`, the Chia farmer and full node, `debug.log`, `chia_recompute_server` in the journal, the broker, and `.env` work.

## Config

`.env` in the working directory, or the environment:

```
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
MQTT_PREFIX=homeassistant
```

Python 3 + `paho-mqtt`. Working directory is the repo (dotenv is `.env` here). systemd stubs are `/root/mqtt-sensors`, run as root. Chia data is under uid 1000's home from `pwd.getpwuid(1000).pw_dir`, never a hardcoded username.
