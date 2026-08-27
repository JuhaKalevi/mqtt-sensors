# mqtt-sensors

Long-lived root processes that publish host stats to MQTT with Home Assistant discovery. One process per collector, one source held open. No framework, no `requirements.txt`, no pip.

Every collector on a host shares one HA device named after the hostname (`linux_host_<hostname>`). The screenshot is the farmer host `M710q` (CPU RAPL + farm size). Recompute and harvester processing times land on whichever machine actually runs those processes.

![Home Assistant](screen.png)

[SECURITY.md](SECURITY.md) is the supply-chain / root policy. [AGENT.md](AGENT.md) is the contract for coding agents adding a sensor — same role as an `AGENTS.md`. You can ignore it.

## Collectors

| collector | what HA shows | source (held open) |
|---|---|---|
| `cpu_package_power.py` | package power (W) and energy (kWh, RAM only, resets on start) | RAPL `energy_uj` fd |
| `nvidia_gpu_power.py` | per-GPU power and energy | one `nvidia-smi --loop=1` |
| `chia_farm_size.py` | plots, on-disk TiB, effective TiB, estimated netspace EiB, ETA to win (s) | farmer `:8559` + full node `:8555` TLS |
| `chia_recompute_server_processing_time.py` | recompute processing time (s, full precision, display 1 decimal) | `journalctl -u chia_recompute_server -f` |
| `chia_harvester_processing_time.py` | harvester processing time (s, full precision, display 1 decimal) | uid 1000 `debug.log` fd, reopen on daily rotate |

Run only the collectors that apply. GPU is a no-op without `nvidia-smi`. Farm needs a local farmer and full node. Recompute needs `chia_recompute_server` in the journal. Harvester follows `plots were eligible for farming … Time: N s` in `debug.log` (via `chia_root()`). Chia paths are uid 1000's `.chia/mainnet`, never `/root` and never a username.

Energy is `total_increasing` kWh integrated in RAM; HA expects it to start at 0. ETA is `(netspace / effective) * 18.75`. Recompute work arrives in 10 s bursts; 0.2 s–5 s is normal — do not average. Harvester samples once per signage point (~every 9 s); a gap or a time climbing toward the signage window is the error signal.

Topics: `$MQTT_PREFIX/sensor/<object>/{config,state}` (default prefix `homeassistant`). Availability is per collector: `gpu_power_<host>`, `chia_farm_<host>`, `chia_recompute_server_<host>`, `chia_harvester_<host>`.

## Run

As root, from `/root/mqtt-sensors`. Distro packages only: `python3` and `python3-paho-mqtt`.

`.env` in the working directory, or the environment:

```
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
MQTT_PREFIX=homeassistant
```

Matching `*.service` stubs: `ExecStart`, `WorkingDirectory=/root/mqtt-sensors`, `Restart=on-failure`, `RestartSec=10`, `WantedBy=default.target`. Enable the ones you want as system units.
