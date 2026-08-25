# mqtt-sensors

Long-lived processes that publish host stats to MQTT with Home Assistant discovery. Absolute minimum. One process per collector, one source held open for the life of that process.

Every collector on a host shares one HA device (`linux_host_<hostname>`), shown as the hostname. This is that device on `M710q`: RAPL package power/energy plus Chia farm plots, on-disk size, effective size, estimated netspace, and ETA to win.

![Home Assistant](screen.png)

## Collectors

| script | source (held open) | objects |
|---|---|---|
| `cpu_package_power.py` | `/sys/class/powercap/intel-rapl:*/energy_uj` (first `package*`) | `cpu_package_power_<host>` + `_energy` |
| `nvidia_gpu_power.py` | one `nvidia-smi --query-gpu=index,name,power.draw --loop=1` | `gpuN_power_<host>`, `gpuN_energy_<host>` per GPU |
| `chia_farm_size.py` | held TLS to farmer `:8559` `get_harvesters_summary` and full node `:8555` `get_blockchain_state` | `chia_plots_<host>`, `chia_farm_size_<host>`, `chia_farm_effective_<host>`, `chia_netspace_<host>`, `chia_eta_<host>` |

Topics under `$MQTT_PREFIX` (default `homeassistant`): `sensor/<object>/{config,state,availability}`. GPU availability is shared: `sensor/gpu_power_<host>/availability`. Chia: `sensor/chia_farm_<host>/availability`.

Power is W (`measurement`). Energy is kWh (`total_increasing`), integrated in RAM from 1 s samples, reset when the process starts. Chia sizes are TiB, netspace is EiB (`data_size`). Plots is a count. ETA to win is seconds (`duration`): `(netspace / effective) * 18.75`. GPU script is a no-op on machines without `nvidia-smi`. Chia needs the farmer, a local full node, and `~/.chia/mainnet` farmer + full_node certs for the user that runs it.

## Adding a sensor

Copy an existing script. Do not add a framework.

1. New script in the repo root. Import from `mqtt_common` only.
2. `get_hostname()` + `make_device()` so unique_ids and the HA device include the host.
3. Hold one source open: a sysfs fd, one subprocess with a native loop (`nvidia-smi --loop=1`), or one HTTP/TLS connection. Never spawn per sample.
4. Connect with LWT on an availability topic. On connect: retained discovery + `online`. On exit: `offline`.
5. ~1 s cadence. Publish retained state. Power is `%.1f` W. If you also publish energy, integrate in RAM (`power * dt / 3600` → kWh) with `state_class=total_increasing`. HA expects that counter to start at 0 when the process starts; do not persist it.
6. Optional `foo.service` stub: `ExecStart`, `WorkingDirectory`, `WantedBy=default.target`. Edit the path on the host. Nothing else.

Reuse `make_power_discovery` / `make_energy_discovery`, or `make_sensor_discovery` for other units. Do not grow `mqtt_common.py` unless several sensors need the same helper.

### Do not

- Comments
- Extra error handling, logging, retries, reconnect wrappers, fake data
- CLI flags, extra config files, new dependencies, tests, types, Docker, CI
- A Sensor base class
- systemd `Restart=`, `[Unit]` filler, hardening

Assume RAPL, `nvidia-smi`, the Chia farmer and full node, the broker, and `.env` work.

## Config

`.env` in the working directory, or the environment:

```
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
MQTT_PREFIX=homeassistant
```

Python 3 + `paho-mqtt`. Working directory is the repo (dotenv is `.env` here). CPU/GPU units stub `/root/mqtt-sensors`. Chia uses `%h/mqtt-sensors` (user home; it does not run as root).
