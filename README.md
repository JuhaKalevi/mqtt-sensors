# mqtt-sensors

Long-lived root processes that publish host stats to MQTT with Home Assistant discovery. One process per collector; each holds open a source or polls one in-process. No framework, no `requirements.txt`, no pip.

Collectors on a host share one HA device named after the hostname (`linux_host_<hostname>`), except device-scoped power-supply batteries, which get their own HA device linked with `via_device`. The screenshot is the farmer host `M710q` (CPU RAPL + farm size). Recompute and harvester processing times land on whichever machine actually runs those processes.

![Home Assistant](screen.png)

[SECURITY.md](SECURITY.md) is the supply-chain / root policy. [AGENT.md](AGENT.md) is the contract for coding agents adding a sensor — same role as an `AGENTS.md`. You can ignore it.

## Collectors

| collector | what HA shows | source (held open) |
|---|---|---|
| `cpu_package_power.py` | package power (W) and energy (kWh, RAM only, resets on start) | RAPL `energy_uj` fd |
| `nvidia_gpu_power.py` | per-GPU power and energy | one `nvidia-smi --loop=1` |
| `chia_farm_size.py` | plots, on-disk TiB, effective TiB, estimated netspace EiB, ETA to win (s) | farmer `:8559` + full node `:8555` TLS |
| `chia_recompute_server_processing_time.py` | recompute processing time (s, full precision, display 1 decimal) | `journalctl -u chia_recompute_server -f` |
| `chia_harvester_processing_time.py` | harvester processing time (s, full precision, display 1 decimal) and plot count | uid 1000 `debug.log` fd, reopen on daily rotate |
| `loginctl_active_users.py` | comma-separated users with `active` or `online` logind sessions | `/run/systemd/users` (what `loginctl` reads; no spawn) |
| `lightdm_active.py` | binary: LightDM unit running | `/sys/fs/cgroup/system.slice/lightdm.service` (no `systemctl`) |
| `power_supply_battery.py` | per-battery capacity (%) | `/sys/class/power_supply` polled each second; `capacity` fd held while the node exists |
| `xmrig_status.py` | hashrate (H/s, 60s), reject ratio (%), mining switch | held HTTP to XMRig `/2/summary` + `/json_rpc` pause/resume |

Run only the collectors that apply. GPU is a no-op without `nvidia-smi`. Farm needs a local farmer and full node. Recompute needs `chia_recompute_server` in the journal. Harvester follows `plots were eligible for farming … Time: N s. Total N plots` in `debug.log` (via `chia_root()`). Chia paths are uid 1000's `.chia/mainnet`, never `/root` and never a username. Power supply batteries are any sysfs node with `type=Battery` and a `capacity` file (Logitech `hidpp_battery_*`, laptop `BAT*`, and the like). The collector rescans that class dir each second so plug/unplug is picked up without a restart, and clears retained discovery when a node disappears. `scope=System` (laptop/UPS) stays on the host HA device; other scopes get their own HA device via the host so a mouse battery does not claim the host device battery badge. XMRig needs its HTTP API on localhost (`XMRIG_HOST`/`XMRIG_PORT`/`XMRIG_TOKEN` in `.env`); the mining switch requires `http.restricted=false` (XMRig is read-only or full write — there is no pause-only ACL). Reject ratio is `(shares_total - shares_good) / shares_total`.

Energy is `total_increasing` kWh integrated in RAM; HA expects it to start at 0. ETA is `(netspace / effective) * 18.75`. Recompute work arrives in 10 s bursts; 0.2 s–5 s is normal — do not average. Harvester samples once per signage point (~every 9 s); a gap or a time climbing toward the signage window is the error signal.

Topics: `$MQTT_PREFIX/sensor/<object>/{config,state}` (default prefix `homeassistant`). Binary sensors use `binary_sensor/<object>/…`. Availability is per collector: `gpu_power_<host>`, `chia_farm_<host>`, `chia_recompute_server_<host>`, `chia_harvester_<host>`, `loginctl_<host>`, `lightdm_<host>`, `power_supply_<host>`, `xmrig_<host>`. Switches use `$MQTT_PREFIX/switch/<object>/{config,state,set}`.

## loginctl active users

`loginctl_active_users.py` does not run `loginctl`. It lists `/run/systemd/users` (numeric files logind already writes), keeps names whose `STATE` is `active` or `online`, sorts them, and publishes a comma-separated string. Lingering with no session is omitted. Unique by username: a second session for the same user does not change the reading.

sshfs/sftp goes through PAM and `pam_systemd`, so a mount account becomes `online` and shows up unless you skip that module for those users. Interactive logins with a seat or pts are left alone.

Dedicated mount group, no TTY, no logind session:

```
groupadd --system sshfs
usermod -aG sshfs mountuser
```

`/etc/ssh/sshd_config.d/sshfs.conf`:

```
Match Group sshfs
    ForceCommand internal-sftp
    PermitTTY no
    AllowTcpForwarding no
    X11Forwarding no
```

Immediately above `pam_systemd.so` in `/etc/pam.d/common-session` (or the file that actually loads it):

```
session [success=1 default=ignore] pam_succeed_if.so quiet user ingroup sshfs
```

`success=1` skips the next line (`pam_systemd`) for that group only. Do not put that skip in front of `@include common-session` — it would skip the whole include. `pam-auth-update` can rewrite `common-session`; re-check the skip after it runs. Then `sshd -t` and reload sshd.

If you sshfs as a user who is already on a seat, they were already in the list; the mount does not add a new name.

## Run

As root, from `/root/mqtt-sensors`. Distro packages only: `python3` and `python3-paho-mqtt`.

`.env` in the working directory, or the environment:

```
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
MQTT_PREFIX=homeassistant
XMRIG_HOST=127.0.0.1
XMRIG_PORT=44444
XMRIG_TOKEN=
```

Matching `*.service` stubs: `ExecStart`, `WorkingDirectory=/root/mqtt-sensors`, `Restart=on-failure`, `RestartSec=10`, `WantedBy=default.target`. Enable the ones you want as system units.
