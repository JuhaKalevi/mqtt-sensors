# AGENT.md

For coding agents. Humans read [README.md](README.md). Security policy: [SECURITY.md](SECURITY.md). Conduct (fail-fast): [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). This file is the `AGENTS.md`-style contract for adding a sensor. Keep it that small.

## Intent

Publish numbers to MQTT so Home Assistant can discover them. Not a framework, not a metrics stack, not a supervisor. Everything here is meant to run as root. If it seems you would not want to be doing what a sensor is doing as root, it probably does not belong in this project.

Because it all runs as root, spare no effort reducing supply-chain risk. Details: `SECURITY.md`. No `requirements.txt`, no pip, no PyPI. Only packages on the distro's standard list. If a sensor's logic depends on something that could be a supply-chain risk, and a small custom solution would be safer, consider that rework. Not necessarily right away — that is the spirit. Stdlib, a sysfs fd, a local log, or a binary already on the host beat a new package. Do not add dependencies. `paho-mqtt` is the exception that already exists and it arrives via the distro (`python3-paho-mqtt`).

## Adding a sensor

1. New script at repo root (host collectors) or under `support/` (shared market/data helpers this project needs). Import `mqtt_common`. Do not add packages.
2. `HOSTNAME = get_hostname()` and `DEVICE, _ = make_device(HOSTNAME)` for host collectors. File: `{source}_{metric}.py`. unique_id and object: `{source}_{metric}_{hostname}`. HA `name` is a readable title that includes the metric (`Chia Recompute Server Processing Time`). Never omit the metric. Never use the raw binary name as the HA name. Availability: `sensor/{source}_{hostname}/availability`. Client id: `{source}-{hostname}`. Host collectors share one HA device. `support/` scripts may use their own HA device (not `linux_host_<hostname>`) when the value is not a host metric.
3. One held-open source for the life of the process: sysfs fd, log fd, one subprocess that loops internally (`nvidia-smi --loop=1`, `journalctl -f`), one HTTP/TLS connection, or an in-process poll of a runtime dir (`/run/systemd/users`, a unit cgroup). **Never** `Popen` per sample. **Never** open a new TCP connection per sample if you can hold one. Reopening a rotated log fd (inode change, once a day) is allowed; do not start `tail`. Do not spawn `loginctl` or `systemctl` to read state.
4. `create_client(..., will_topic=availability)` then `loop_start()`. On connect: retained discovery JSON + `online`. On exit: `offline`, stop, disconnect.
5. ~1 s using `time.monotonic()` for `dt` (host collectors). `support/` price feeds may poll slower (e.g. 60 s). Drive temperature polls every 60 s (`DRIVE_TEMPERATURE_INTERVAL`) because reads can reset drive spin-down timers. Retained state.
6. Power: W, `make_power_discovery`, `%.1f`.
7. Energy (if any): RAM only, `energy_wh += power * (dt / 3600.0)`, publish `energy_wh / 1000.0` as kWh `%.6f`, `make_energy_discovery` (`total_increasing`). It resets when the process starts. That is correct for HA. Do not write it to disk.
8. Optional matching `*.service`: `ExecStart`, `WorkingDirectory`, `Restart=on-failure`, `RestartSec=10`, `[Install] WantedBy=default.target`. Path stub `/root/mqtt-sensors`. All collectors run as root. Do not add `User=` / `Group=`. Chia paths: `chia_root()` from `mqtt_common` (`pwd.getpwuid(1000).pw_dir / ".chia" / "mainnet"`). Never a username, never `/home/…`, never `Path.home()`.

Other measurement types: `make_sensor_discovery(...)` with the HA unit / device_class / state_class. Do not extend `mqtt_common.py` for a one-off. Switches use `make_switch_discovery(...)`, `switch_mode(env_name)`, and `make_switch_on_message(...)`; systemd units use `systemd_unit_state(unit)` (cgroup dir, no `systemctl`) and `systemd_unit_command(unit, payload)`. Power supply batteries are `%` / `battery`. Drive temperatures are `°C` / `temperature`, `%.1f`. Chia plots omit `device_class` (plain count). Chia sizes use `TiB` / `data_size`, netspace `EiB` / `data_size`, ETA `s` / `duration`. Recompute and harvester processing time are `s` / `duration`; publish full precision, set `suggested_display_precision` to 1 on that dict. One sensor per process, except `chia_harvester_processing_time.py` (plot count from the same `debug.log` line) and `xmrig_status.py` (hashrate + reject ratio + mining switch + profitability factor from XMRig HTTP plus MQTT inputs). Publish each sample; do not average. Do not publish fail or gpu flags; gaps in time are enough. Loginctl active users is a string (comma-separated names); no unit, no device_class, no state_class. On/off services are MQTT `binary_sensor` (`device_class=running`, payloads `ON`/`OFF`); config under `binary_sensor/<object>/config`. Controllable on/off is MQTT `switch` under `switch/<object>/{config,state,set}`; XMRig mining is the first (`ON`=resume, `OFF`=pause). LightDM active and Ollama active are switches whose state is the `lightdm.service` / `ollama.service` cgroup dir existing; `LIGHTDM_SWITCH_MODE` / `OLLAMA_SWITCH_MODE` `read_only` (default) ignores commands and re-publishes state, `control` runs `systemctl start|stop <unit>` on a command only (never per sample). XMRig hashrate is `H/s`, reject ratio `%` from `(shares_total - shares_good) / shares_total`. Profitability factor is unitless `revenue/cost` (`hashrate * xmr_per_hs_day / 24 * xmr_eur` over `package_W/1000 * €/kWh`); while paused, last mining hashrate and package watts are kept in RAM so the factor does not collapse. Publish only — no built-in pause/resume from the factor; HA (or later logic) chooses thresholds. Fiat prices are `EUR` / `monetary`; XMR/EUR uses Kraken public ticker last trade. Monero network: difficulty (no unit), network hashrate `H/s`, last-block coinbase reward `XMR`, and `XMR/H/s/d` = `reward * 86400 / difficulty` from xmrchain.net.

## Hard rules

- No comments.
- Minimal error handling. Assume RAPL, `nvidia-smi`, the Chia farmer on localhost:8559, full node on localhost:8555, farmer + full_node certs and `debug.log` under uid 1000's `.chia/mainnet`, `journalctl -u chia_recompute_server`, `/run/systemd/users`, `/sys/fs/cgroup/system.slice/{lightdm,ollama}.service`, `/sys/class/power_supply/*/capacity` for `type=Battery`, `/sys/class/hwmon/*/temp1_input` for `drivetemp`/`nvme`, `SG_IO` on `/dev/sd*` as root, XMRig HTTP API on `XMRIG_HOST`:`XMRIG_PORT` (default `127.0.0.1:44444`) with `XMRIG_TOKEN` when set, Home Assistant `/api/states/<entity>` when `HA_TOKEN` is set, Kraken `api.kraken.com` public ticker, xmrchain.net `/api/networkinfo` + `/api/block/<height-1>`, the broker, and `.env` work.
- No logging, retries, backoff, reconnect logic, tests, types, CLI flags, extra config, or dependencies beyond distro `python3-paho-mqtt`. Never add `requirements.txt`.
- Publish-only is the default. Anything that acts on the host (runs `systemctl`, controls a process, anything an MQTT command triggers) needs an explicit opt-in `.env` setting that is off when unset or empty, and runs only on the command, never per sample. That setting is the one allowed extra config. Pattern: `LIGHTDM_SWITCH_MODE=control`. XMRig mining predates this rule and has no opt-in yet.
- Do not add systemd hardening, `[Unit]` keys, or healthchecks. Units do use `Restart=on-failure` and `RestartSec=10`.
- Do not drop root or add `User=`. If the work should not run as root, it is the wrong repo.
- Treat supply-chain risk as a reason to rewrite a sensor in stdlib rather than to import one more thing. Existing sensors need not be rewritten on sight.
- Do not persist energy, add `last_reset`, MQTT TLS, or HA extras unless asked.
- Do not introduce a Sensor class. Different sources get their own small scripts.
- Code reuse: each source gets its own small script and its own normal (non-template) systemd unit that can be enabled from the repo path. Do not generalize whole collectors (no template units, no per-instance settings). But when two scripts share logic that is plausibly useful elsewhere (reading unit state, a switch mode check, switch discovery/command handling), extract that part into `mqtt_common.py` instead of copying it, as a building block something else can easily leverage later, not a merged collector (`CODE_OF_CONDUCT.md`, Keep AGENT.md small). Leave the rest of each script explicit.
- Leave existing comments and defensive parsing in the current scripts. Do not clean them up and do not copy them into new ones.

## Layout

- `mqtt_common.py` — dotenv, hostname, discovery, client + LWT, `chia_root()` (uid 1000), switch helpers (discovery, `read_only|control` mode, command handling), systemd unit state (cgroup) + start/stop
- `cpu_package_power.py` — RAPL package, fd held open, wrap via `(curr - prev) % max_energy_range_uj`
- `nvidia_gpu_power.py` — one `nvidia-smi --loop=1`, multi-GPU
- `chia_farm_size.py` — held HTTPS to farmer `get_harvesters_summary` and full node `get_blockchain_state`; plots + TiB + effective TiB + netspace EiB + ETA seconds `(space/effective)*18.75`; certs under uid 1000 home
- `chia_farm_size.service` — `/root/mqtt-sensors`
- `chia_recompute_server_processing_time.py` — one `journalctl -u chia_recompute_server -f`, each line → full-precision seconds, display 1 decimal
- `chia_recompute_server_processing_time.service` — `/root/mqtt-sensors`
- `chia_harvester_processing_time.py` — held `debug.log` fd under uid 1000 home, eligible-plots lines → processing time (s) + plot count (`Total N plots` on the same line; allowed two-sensor exception), reopen on inode change
- `chia_harvester_processing_time.service` — `/root/mqtt-sensors`
- `loginctl_active_users.py` — `/run/systemd/users`, names with state `active`/`online`, comma-separated; do not spawn `loginctl`. sshfs: skip `pam_systemd` for group `sshfs` (README).
- `loginctl_active_users.service` — `/root/mqtt-sensors`
- `lightdm_active.py` — cgroup dir for `lightdm.service` → MQTT switch ON/OFF; clears the old binary_sensor discovery; `LIGHTDM_SWITCH_MODE=control` starts/stops the unit
- `lightdm_active.service` — `/root/mqtt-sensors`
- `ollama_active.py` — cgroup dir for `ollama.service` → MQTT switch ON/OFF; `OLLAMA_SWITCH_MODE=control` starts/stops the unit
- `ollama_active.service` — `/root/mqtt-sensors`
- `hwmon_drive_temperature.py` — `/sys/class/hwmon` nodes named `drivetemp`/`nvme` (`temp1_input` fds), else every non-removable `/sys/block/sd*` not covered by drivetemp via an in-process `SG_IO` ioctl on a held `/dev/sdX` fd (ATA PASS-THROUGH 16, fallback 12; read-only CHECK POWER MODE each sample, skip if it reports standby; otherwise, also when the bridge cannot report it, IDENTIFY once and SMART READ DATA, attr 194 else 190). Not a subprocess; still publish-only; may keep drives spinning behind bridges without power-state reporting. Rescan and re-check `device` links each sample; unique_id from the ATA serial (`vpd_pg89` or IDENTIFY), then `serial`/`vpd_pg80`, `wwid`, USB `idVendor`+`idProduct`+`serial`+LUN (NVMe `serial`), never `sdX`; `(USB)` in the name; one printed line per drive outcome, none per sample; unsupported (read impossible) drives not retried until they reappear; exits only with no hwmon and no candidate disk; `DRIVE_TEMPERATURE_DEBUG=1` prints each probe SG_IO (CDB, status, sense)
- `hwmon_drive_temperature.service` — `/root/mqtt-sensors`
- `power_supply_battery.py` — every `/sys/class/power_supply` node with `type=Battery` and `capacity`; rescans the class dir each second (add/remove), holds `capacity` fds while present, clears MQTT discovery on remove; `scope=System` stays on the host HA device, anything else gets its own HA device with `via_device`
- `power_supply_battery.service` — `/root/mqtt-sensors`
- `xmrig_status.py` — held HTTP to local XMRig `/2/summary` + `/json_rpc`; hashrate (60s), reject ratio, mining switch always; profitability factor when spot is available (default: HA `sensor.porssisahko_electricity_price` via `HA_TOKEN`/`HA_URL`, else `ELECTRICITY_EUR_PER_KWH_TOPIC` or `ELECTRICITY_EUR_PER_KWH`) and retained MQTT has package power, `xmr_per_hs_day`, XMR/EUR; discovery on first successful factor; remembers last mining hashrate + package W while paused; needs `http.restricted=false` for the switch
- `xmrig_status.service` — `/root/mqtt-sensors`
- `support/` — helpers that publish MQTT for this project but are not host collectors; global MQTT object ids / unique_ids (no hostname). Client id may still include hostname so two hosts do not collide if both run a helper
- `support/xmr_eur_price.py` — held HTTPS to Kraken public `XMREUR` ticker, last trade as EUR, own HA device `XMR/EUR`, global object id `xmr_eur_price` (no hostname), 60 s poll
- `support/xmr_eur_price.service` — `/root/mqtt-sensors`
- `support/xmr_network.py` — held HTTPS to xmrchain.net; difficulty, network hashrate, last-block coinbase reward (XMR), XMR per H/s per day; own HA device `Monero`; global object ids (no hostname); 60 s poll
- `support/xmr_network.service` — `/root/mqtt-sensors`
- `*.service` — path stubs under `/root/mqtt-sensors`
- `.env` — gitignored
- `SECURITY.md` — root + supply-chain rules, distro packages only
- `CODE_OF_CONDUCT.md` — fail-fast, minimal surface, assume prerequisites; systemd restarts
- `windows/` — Windows copies of the CPU/GPU power collectors; Linux scripts are the reference; share `mqtt_common` only; see `windows/README.md`
- `screen.png` — example HA device page (farmer host `M710q`)
- `screen2.png` — example HA device page (miner host `B850Pro`, XMRig + profitability)
