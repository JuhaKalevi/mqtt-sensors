# mqtt-sensors

Long-lived root processes that publish host stats to MQTT with Home Assistant discovery. One process per collector; each holds one source open or polls one in-process. No framework, no `requirements.txt`, no pip.

- [SECURITY.md](SECURITY.md): supply-chain / root policy.
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md): fail-fast and minimalism.
- [AGENT.md](AGENT.md): contract for coding agents adding a sensor (an `AGENTS.md`).
- [windows/](windows/README.md): Windows power and XMRig collectors plus a tray launcher.

Collectors on a host share one HA device `linux_host_<hostname>`, named after the host. Device-scoped power-supply batteries get their own HA device linked with `via_device`.

![Home Assistant — M710q](screen.png)

![Home Assistant — B850Pro](screen2.png)

## Collectors

| collector | what HA shows | source (held open) |
|---|---|---|
| `cpu_package_power.py` | package power (W), energy (kWh) | RAPL `energy_uj` fd |
| `nvidia_gpu_power.py` | per-GPU power (W), energy (kWh) | one `nvidia-smi --loop=1` |
| `chia_farm_size.py` | plots, on-disk TiB, effective TiB, netspace EiB, ETA to win (s) | farmer `:8559` + full node `:8555` TLS |
| `chia_recompute_server_processing_time.py` | recompute processing time (s) | `journalctl -u chia_recompute_server -f` |
| `chia_harvester_processing_time.py` | harvester processing time (s), plot count | uid 1000 `debug.log` fd, reopened on daily rotate |
| `loginctl_active_users.py` | comma-separated users with `active`/`online` sessions | `/run/systemd/users` |
| `lightdm_active.py` | switch: LightDM running | `lightdm.service` cgroup dir |
| `ollama_active.py` | switch: Ollama running | `ollama.service` cgroup dir |
| `hwmon_drive_temperature.py` | per-drive temperature (°C) | `/sys/class/hwmon` + SAT `SG_IO`, every 60 s |
| `power_supply_battery.py` | per-battery capacity (%) | `/sys/class/power_supply`, each second |
| `kmsg_reset_reason.py` | previous system reset reason (text, hex `code` attribute), once per boot | `/dev/kmsg` |
| `xmrig_status.py` | hashrate (H/s, 60 s), reject ratio (%), mining switch, intensity slider (0–100%), profitability factor | XMRig HTTP + MQTT inputs |
| `support/xmr_eur_price.py` | XMR/EUR (EUR) | Kraken public ticker, 60 s |
| `support/xmr_network.py` | Monero difficulty, nominal / difficulty-period / current block time (s), block reward (XMR), XMR/H/s/day | xmrchain.net, 60 s |
| `support/pearl_usdt_price.py` | PEARL/USDT `Price` (avg/max/min of sources) + `Gate Price` + `NEAR Intents Price` (USDT) | Gate public spot ticker, NEAR Intents 1Click token prices, 60 s |
| `support/usdt_eur_price.py` | USDT/EUR `Price` (avg/max/min of sources) + `Kraken Price` (EUR) | Kraken public ticker, 60 s |
| `support/pearl_network.py` | Pearl difficulty, nominal / difficulty-period / current block time (s), block reward (PEARL), PEARL/H/s/day | pearlchain.live, 60 s |
| `grok_usage.py` | Grok subscription credit usage (%), reset time and per-product shares as attributes | Grok CLI billing endpoint, `GROK_USAGE_INTERVAL` |

Run only the collectors that apply. Energy is `total_increasing` kWh integrated in RAM and resets on start; HA expects that. Processing times publish full precision with 1 decimal displayed; do not average.

- **Chia:** paths are uid 1000's `.chia/mainnet` (`chia_root()`), never `/root` or a username. ETA is `(netspace / effective) * 18.75`. Recompute work arrives in 10 s bursts; 0.2–5 s is normal. Harvester parses `plots were eligible for farming … Time: N s. Total N plots`, about every 9 s; a gap or a time climbing toward the signage window is the error signal.
- **Batteries:** any sysfs node with `type=Battery` and `capacity` (Logitech `hidpp_battery_*`, laptop `BAT*`). Rescanned each second; retained discovery is cleared when a node disappears. `scope=System` stays on the host device; others get their own device. Nodes with the same `model_name` are one battery, so a mouse plugged in by cable does not add an entity.
- **`support/`:** helpers that are not host metrics (global object ids, no hostname). Expected XMR/day ≈ local hashrate × XMR/H/s/day (`reward * 86400 / difficulty`). Expected PEARL/day ≈ miner-reported hashrate × PEARL/H/s/day (`reward * 86400 / (difficulty * 2^64 / 65535)`; Pearl "H/s" counts multiply-accumulates, so the miner's TH/s and the network hashrate share a unit). Each coin publishes three block times (s): **nominal** (the fixed coin parameter), **difficulty-period** (what the current difficulty is set for; equals nominal unless a DAA change cap was hit; Pearl and Monero have no such cap), and **current** (a loose recent average; window in `window_secs`/`window_blocks` attributes). Network hashrate is not published; implied current hashrate is work per block / current block time (or target hashrate × nominal / current) and can be derived in HA if needed. Data fetched from the internet (network stats, prices) lives in `support/`; values a local node reports directly (Chia netspace from the farmer's own full node) stay on the host device. Market devices are real exchange-traded pairs only (`XMR/EUR`, `PEARL/USDT`, `USDT/EUR`), one device per pair with a generic `Price` (avg, max or min of its per-source `<Source> Price` entities, per `<BASE>_<QUOTE>_PRICE_MODE`); a fiat value multiplies real steps (PEARL/USDT × USDT/EUR), each discounted by its own `slippage_pct` attribute (set per pair in `.env`); each coin's network stats are one device (`Monero`, `Pearl`).

Topics: `$MQTT_PREFIX/{sensor,binary_sensor,switch,number}/<object>/…` with `config`, `state` and, for switches and numbers, `set`. Availability is per collector under `sensor/<name>_<host>/availability`: `gpu_power`, `chia_farm`, `chia_recompute_server`, `chia_harvester`, `loginctl`, `ollama`, `power_supply`, `hwmon_drive_temperature`, `xmrig`, `xmr_eur`, `xmr_network`, `pearl_usdt`, `usdt_eur`, `pearl_network`; `grok` uses `sensor/grok/availability` with no host; LightDM keeps `binary_sensor/lightdm_<host>/availability` from before it became a switch.

## XMRig

Needs the XMRig HTTP API (`XMRIG_HOST`/`XMRIG_PORT`, `XMRIG_TOKEN` when set). The mining switch and intensity slider need `http.restricted=false` (XMRig has no pause-only ACL).

- **Switch:** `ON` resumes, `OFF` pauses.
- **Intensity:** threads = `floor(percent * max_threads / 100)` on the active CPU profile via `GET/PUT /2/config`; `max_threads` is the OS logical CPU count (`os.cpu_count()`), never XMRig's current or configured thread count, so a profile saved below 100% (autosave) cannot shrink it; a shorter list profile is padded with affinity `-1` entries. The minimum is 1 thread (0% sets 1); the mining switch pauses. With XMRig `autosave`, it persists to `config.json`.
- **Reject ratio:** `(shares_total - shares_good) / shares_total`.
- **Profitability factor:** `revenue / cost` (1.0 ≈ break-even), revenue `hashrate * xmr_per_hs_day / 24 * xmr_eur`, cost `mining_W / 1000 * €/kWh`. Needs a spot price source (see Configuration) plus MQTT package power (every second, not retained), and retained `xmr_per_hs_day` and XMR/EUR; missing inputs are printed once a minute. Publish-only, no automation.
- **Mining power:** raw `package_W` until a 100%-intensity baseline exists. The baseline is the max `package_W` over the first ~5 min at 100% (not paused), or `XMRIG_POWER_BASELINE_W` right away. Then `package_W` at 100%, `min(package_W, f * baseline)` when `f = threads/max_threads < 1`, 0 at 0%. While paused, the last hashrate and mining watts are kept in RAM.

Contention handling is shared by Linux and Windows (`MiningIntensity` and `Contention` in `xmrig_common.py`) and runs with `XMRIG_CONTENTION_MODE=control`. `f = threads / max_threads`; hashrate means the 30 s average of the 10 s hashrate.

- **Baseline and plateau:** max package W and max 10 s hashrate over the first ~5 min at 100%, then a ±5% drift check every ~5 min against the rolling ~15 min max. `XMRIG_POWER_BASELINE_W` / `XMRIG_HASHRATE_PLATEAU_HS` hints apply from the start; a measurement replaces a hint only within ±5% (else it is rejected and logged), so set hints close to the true values.
- **Pause:** judging and timers hold on a thread change ramp, a pool/connection change (`connection.pool`/`ip` change or `uptime` reset in `/2/summary`, which covers the dev fee switch), 0 H/s, or 10 s samples over 30 s deviating more than 5% from their mean, until 15 samples are within ±5%. 0 H/s, pool changes and pauses over 60 s are logged.
- **contended:** hashrate / plateau below `0.93 * f` (clears above `0.97 * f`) for 15 s. Threads drop once a minute by logical threads per physical core (`cpu.threads // cpu.cores`), never below 1.
- **balanced:** after a drop, no hashrate gap but the 5 min load average rounded (half up) above the thread count: other work still competes, hold threads. Leaves on a 60 s hashrate gap (back to dropping), when rounded load is at or below the thread count, or as an I/O-stall override when power stays below `0.85 * f * baseline` for 30 s. Windows has no load average: it holds while power is above `1.10 * f * baseline` and leaves after 30 s at or below it.
- **recovering:** after the wait (5 min) add one thread, then one per uncontested minute up to 100% (**tracking**). Contention right after a raise doubles the wait (5, 10, 20 min, …); 15 min calm at 100% or a slider move resets it.
- State changes log the state, reason and time in the previous state. A slider command cancels the automatic state; the slider always shows the threads XMRig runs.

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

## Input APM

`input_apm.py` reads keyboard and mouse events straight from `/dev/input/event*` (root, standard library, no X). Every 5 s it publishes mouse, keyboard and total APM: button and key presses in the last 60 s. Key repeats do not count. Which key was pressed is never stored or published. Devices plugged in later are picked up within 5 s.

## Previous system reset reason

`kmsg_reset_reason.py` reads the current boot's kernel ring buffer from `/dev/kmsg` once, finds `x86/amd: Previous system reset reason [0x…]: …` and publishes the reason text as state with the raw hex as the `code` attribute, all retained, then exits. No availability topic, so the value stays after the process ends. Without that line (clean poweroff) it publishes `clean shutdown` with a null `code`. AMD hosts only: on other CPUs or older kernels the line never appears, so it would always say `clean shutdown`; do not enable it there.

`kmsg_reset_reason.service` is `Type=oneshot` after `network-online.target`; `Restart=on-failure` covers a broker that is not up yet. Enable it so it runs on every boot (`--now` also publishes the current boot):

```
systemctl enable --now /root/mqtt-sensors/kmsg_reset_reason.service
```

## Unit switches (LightDM, Ollama)

`lightdm_active.py` publishes a HA `switch` (`switch/lightdm_active_<host>/{config,state,set}`). State is the `lightdm.service` cgroup dir existing, checked each second. On connect it clears the retained discovery and state of the old `binary_sensor/lightdm_active_<host>` so HA drops that entity.

`ollama_active.py` does the same for `ollama.service`: `switch/ollama_active_<host>/{config,state,set}`, HA name `Ollama Active`, availability `sensor/ollama_<host>/availability`. Enable it only on hosts that have `ollama.service` as a system unit.

Each has its own opt-in in `.env`, `LIGHTDM_SWITCH_MODE` and `OLLAMA_SWITCH_MODE`:

- `read_only` (default, also when unset or empty): HA requires a `command_topic`, so the switch has one, but commands are ignored (printed) and the real state is re-published, so the toggle snaps back.
- `control`: `ON` runs `systemctl start <unit>`, `OFF` runs `systemctl stop <unit>` (`lightdm.service` / `ollama.service`), prints the action and exit code, then re-publishes the real state. The collector runs as root, so no sudoers or polkit rule is needed. For LightDM, `OFF` ends any graphical session on that seat.

Any other value exits. The mode check and the switch discovery/command handling are small helpers in `mqtt_common.py`, and service state/start/stop go through `service_manager()` (see Other init systems); each script only names its service, entity, and setting.

```
systemctl enable --now /root/mqtt-sensors/ollama_active.service
```

Upgrading a LightDM host: nothing changes for HA or `.env` (same entity, topics, and `LIGHTDM_SWITCH_MODE`). `cd /root/mqtt-sensors && git pull && systemctl restart lightdm_active` picks up the new `mqtt_common.py`.

## Other init systems

Every systemd interaction goes through an interface in `mqtt_common.py`; collectors never call systemd tools or read its files directly:

- `service_manager()`: `is_active(name)`, `start(name)`, `stop(name)`, `follow_log(name)` (context manager yielding new log lines). `name` is the bare service name (`lightdm`, `ollama`, `chia_recompute_server`). Shipped backend `SystemdServiceManager`: cgroup dir read, `systemctl start|stop`, `journalctl -u <name> -f -n 0 -o cat`. Used by `lightdm_active.py`, `ollama_active.py`, `chia_recompute_server_processing_time.py`.
- `session_source()`: `active_users()` (names with an `active` or `online` session). Shipped backend `LogindSessionSource`: `/run/systemd/users`, which elogind also writes, so elogind hosts need nothing. Used by `loginctl_active_users.py`.

For OpenRC, runit, s6 or another init, write a class with the same methods and return it from that function; the collectors stay untouched. Those backends are not provided here. The `*.service` files are systemd units; other init systems need their own service definitions.

## Hardware sensors

`hwmon_sensors.py` publishes every hwmon temperature (°C), fan (RPM) and voltage (V) every 5 s from held sysfs fds: CPU (`k10temp` Tctl/Tccd, `coretemp`), the board Super-I/O chip (whatever driver is loaded: `nct6775`, `nct6687`, `it87`, …), `amdgpu`, and so on. Drives (`drivetemp`, `nvme`) are left to the drive temperature collector; `nouveau` is skipped. No subprocess. Names are `<chip> <label> <kind>`; unique_id `hwmon_<chip>_<input>_<hostname>`. Hide unwired fan headers or bogus voltages with `HWMON_SENSORS_EXCLUDE=nct6798/fan5,nct6798/in7` (`chip/label`; the label is the kernel `_label`, else the input name). Labels are what the sensor names show, so the list reads like the HA names.

`NVIDIA_GPU_STATS=1` extends `nvidia_gpu_power.py` with GPU temperature, memory temperature, fan %, core/memory clock, utilization, power limit, pstate and throttle reasons, all from the same `nvidia-smi --loop=1`. Empty keeps power + energy only. Fields the GPU reports as `[N/A]` get no entity.

## Drive temperature

On USB bridges that cannot report the drive's power state, this collector reads the drive every interval and may keep it from spinning down. If that matters, raise `DRIVE_TEMPERATURE_INTERVAL` or do not run the collector on that host.

`hwmon_drive_temperature.py` runs no subprocess (no `smartctl`). Two sources, one `°C` / `temperature` sensor per drive on the host HA device:

1. **hwmon:** `temp1_input` (millidegrees C) of every `/sys/class/hwmon/hwmon*` named `drivetemp` (SATA, also behind SAS HBAs and some USB bridges) or `nvme`. This is preferred when present.
2. **SAT fallback:** every `/sys/block/sd*` not already covered by a `drivetemp` hwmon (same SCSI device path), not `removable`, with non-zero size. The collector holds `/dev/sdX` open (`O_RDONLY|O_NONBLOCK`) and sends read-only ATA commands in-process with the `SG_IO` ioctl (stdlib `fcntl` + `ctypes`) wrapped in SCSI ATA PASS-THROUGH(16), falling back to (12). This works without `drivetemp` and on `usb-storage` bridges where `drivetemp` never attaches. Needs root (`CAP_SYS_RAWIO`), which every collector here has.

SAT per sample: CHECK POWER MODE (`E5h`, non-data, `CK_COND` so the bridge returns the ATA registers; CDB flags byte `2Ch` as smartmontools sends it). If it answers standby (`00h`/`01h`) or another non-active value, nothing more is sent and nothing is published; HA keeps the last value. If it answers active/idle (`FFh`, `80h`–`83h`, `41h`), or the bridge cannot answer at all (error, no or invalid registers), the collector goes on: IDENTIFY DEVICE (`ECh`) once per appearance for the real ATA serial/model and the SMART support/enabled bits, then SMART READ DATA (`B0h`/`D0h`) every sample, publishing raw byte 0 of attribute 194, else 190, if 1–99 °C.

Ids: the ATA serial (and full ATA model) from `vpd_pg89` for `drivetemp`, or from IDENTIFY for SAT, so both paths give the same id. Then the SCSI `serial` / `vpd_pg80`, `wwid`, then for USB the enclosure's `idVendor` + `idProduct` + `serial` + LUN. Nothing stable: skipped; `sdX` is never used. NVMe uses the controller's `model` + `serial`. Object and unique_id are `hwmon_drive_temperature_<id>_<host>`. USB drives get ` (USB)` in the HA name. `/sys/class/hwmon` and `/sys/block` are rescanned each sample (`device` links re-checked), so drives come and go, and a reused `hwmonN` / `sdX` is a new drive. A failed read skips that sample for that drive only. It exits only if there is no `drivetemp`/`nvme` hwmon and no candidate `sd*` disk.

Drives that are gone (another enclosure, a replaced disk) leave entities behind: early versions retained discovery, and states are retained, so HA recreates them on every restart. `hwmon_drive_temperature.py` has no automatic sweep (a drive in standby publishes nothing until it spins up, so "not published" does not mean "gone"); clear them by hand instead:

```sh
./hwmon_drive_temperature.py --list            # present drives + every own retained object, marked present / stale
./hwmon_drive_temperature.py --purge-stale     # empty config + cleared retained topics for the stale ones only
python3 mqtt_purge_retained.py --script hwmon_drive_temperature.py --delete --stale   # same, via the purge tool
python3 mqtt_purge_retained.py --entity sensor/hwmon_drive_temperature_<id>_<host> --delete   # a ghost with nothing retained
```

The present drives' object ids come from sysfs only (no ATA/SCSI command, nothing opened under `/dev`, so a standby drive is not woken): `drivetemp`/`nvme` hwmon drives through the same `identity()` the collector uses, every other `sd*` disk through `identity()` as well, which uses the IDENTIFY data the kernel caches in `vpd_pg89`, the same serial the collector's SAT IDENTIFY gives. A candidate `sd*` disk without `vpd_pg89` (typically behind a USB bridge) may have a different id from IDENTIFY, so `--list` shows it as `unknown` and `--purge-stale` refuses (exit 1) while it is attached; remove single ghosts with `--entity` then. Scope is objects `hwmon_drive_temperature_*_<host>` with an exact `_<host>` suffix, so another host's drives are never touched; the availability topic is never touched. A ghost with no retained topic at all cannot be seen over MQTT: take its object id from the HA entity id (`sensor.<object id>`) and use `--entity`. `--purge` removes all of this host's drive entities, present ones too (they come back with history lost when the collector runs again). Stop the collector before either purge (`systemctl stop hwmon_drive_temperature`), or a drive it is reading recreates its entity.

Output: one summary line at start, then one line per drive when it is first seen, plus one when a standby drive is later identified. No per-sample output:

```
hwmon_drive_temperature: host=X570AorusElite interval=60s drivetemp/nvme hwmon=1 scsi disks=16
hwmon_drive_temperature: nvme0: reading via nvme hwmon, Samsung SSD 980 PRO 1TB S5GXNX0T123456A
hwmon_drive_temperature: sda: reading via SAT ATA_16, WDC WD40EFRX-68N32N0 WD-WCC7K1234567
hwmon_drive_temperature: sdb: in standby (power mode 0x00), not reading until it spins up
hwmon_drive_temperature: sdc: unsupported: ATA_16: ATA pass-through rejected (ILLEGAL REQUEST); ATA_12: ATA pass-through rejected (ILLEGAL REQUEST)
hwmon_drive_temperature: sdd: power mode unknown (ATA_16: bridge returns no ATA registers; ATA_12: bridge returns no ATA registers), reading anyway; may keep drive spinning
hwmon_drive_temperature: sde: skipped: removable or no media
```

A drive is `unsupported` only when the temperature read itself cannot work: pass-through rejected on both ATA_16 and ATA_12, an SG_IO errno, SMART unsupported or disabled, or no attribute 194/190. It is not retried until it disappears and comes back (or the service restarts). On a drive that already reads, a failed sample is just skipped.

`DRIVE_TEMPERATURE_DEBUG=1` (default off, read-only): for each drive on first contact, one line with the SCSI device path, USB driver (`uas` / `usb-storage`), `idVendor:idProduct`, whether `vpd_pg89` exists and `usb-storage.quirks`. Then one line per SG_IO attempt (probe and identification only, not per sample) with the CDB, ioctl errno, `status`, `masked_status`, `host_status`, `driver_status`, `sb_len_wr`, `resid`, `duration`, the sense bytes and the first 16 data bytes. Reading it:

- `errno=EPERM`/`EACCES`: not root. `ENOTTY`/`EINVAL`: that device node does not take SG_IO.
- `host=0x0003` (timeout) or other non-zero `host`: the bridge/USB link failed the command; nothing from the drive.
- `status=0x02`, sense `72 05 20 00` / `70 00 05 … 20 00` (ILLEGAL REQUEST, invalid opcode) or `… 24 00` (invalid field in CDB): the bridge (or the kernel for `NO_ATA_1X` quirk devices) refuses that pass-through CDB. If both ATA_16 and ATA_12 do, the bridge has no usable SAT.
- `status=0x02`, sense starting `72 01 00 1d` (descriptor) with `09 0c …` or `70 00 01 … 00 1d` (fixed): registers came back; the count byte is the power mode (`ff` active, `00` standby).
- `status=0x00`, `sb_len_wr=0` for `E5h`: the command went through but the bridge ignored `CK_COND` and returned no registers. Common on cheap bridges. The power state is unknown and the drive is read anyway.

`drivetemp` is optional now. Load it if you prefer the kernel path for internal SATA:

```
modprobe drivetemp
echo drivetemp > /etc/modules-load.d/drivetemp.conf
```

Standby and spin-down: CHECK POWER MODE "shall not cause the device to change its power management state or affect the operation of the Standby timer" (ATA8-ACS / ACS-2 §7.8.2, T13/2015-D). smartmontools checks power mode the same way before anything else with `smartctl -n` / smartd `-n` ([ataprint.cpp](https://github.com/smartmontools/smartmontools/blob/master/smartmontools/ataprint.cpp), [smartd.cpp](https://github.com/smartmontools/smartmontools/blob/master/smartmontools/smartd.cpp)). smartctl's man page warns that the device "may spin up due to commands issued during device type autodetection"; IDENTIFY and SMART READ DATA are sent only after an active report, or when the bridge cannot report the power state at all. Reading SMART or drivetemp on an active drive may still reset its spin-down timer (kernel [drivetemp](https://docs.kernel.org/hwmon/drivetemp.html) usage note, seen on WD120EFAX): read at intervals longer than twice the spin-down time or affected drives never spin down. Default is 60 s; set `DRIVE_TEMPERATURE_INTERVAL` (seconds) higher if drives should sleep.

### USB drives

`drivetemp` needs VPD page 0x89 (`drivetemp_identify_sata()` in [drivetemp.c](https://github.com/torvalds/linux/blob/master/drivers/hwmon/drivetemp.c)). `usb-storage` (BOT) sets `skip_vpd_pages` ([scsiglue.c](https://github.com/torvalds/linux/blob/master/drivers/usb/storage/scsiglue.c)), so most USB disks have no `vpd_pg89` and no `drivetemp` hwmon. They go through the SAT fallback instead. That sends the same ATA PASS-THROUGH a `smartctl -d sat` would, in-process.

Known not to work:

- Bridges with the `NO_ATA_1X` quirk (`t` in `usb-storage.quirks`; ADATA CH94, Initio INIC-3069, PNY Elite, VIA VL711 in [unusual_uas.h](https://github.com/torvalds/linux/blob/master/drivers/usb/storage/unusual_uas.h)): the kernel rejects pass-through. Logged as `unsupported`.
- Bridges without SAT pass-through: `unsupported`. Bridges that pass SMART through but return no ATA registers for CHECK POWER MODE are read anyway (see the top of this section).
- USB-NVMe enclosures (JMicron JMS583, Realtek RTL9210, ASMedia ASM236x): not ATA. `smartctl` needs vendor pass-through (`-d sntjmicron` / `sntrealtek` / `sntasmedia`, e.g. `sntrealtek` since smartmontools [7.2](https://github.com/smartmontools/smartmontools/releases/tag/RELEASE_7_2)), not implemented. Expect `unsupported`.
- USB sticks and card readers (`removable=1`) are skipped.
- A drive put into SLEEP (`hdparm -Y`), not standby, cannot answer CHECK POWER MODE, so it counts as unknown and the SMART read will wake it (or fail).

When USB SAT temperature reads fail during startup on `uas` disks, the collector keeps each VID:PID and logs them once at the end of init, for example:

```
usb-storage quirks suggested: 174c:55aa:u,0bc2:2320:u
```

If none fail, it logs nothing.

The collector only reads sysfs and does not apply the quirk. An administrator can apply it with a modprobe configuration such as `options usb-storage quirks=174c:55aa:u`, or with `usb-storage.quirks=174c:55aa:u` on the kernel command line when `usb-storage` is built in. It takes effect on the next probe (replug or reboot), applies to every enclosure with that VID:PID, and `/dev/sdX` names may change.

Why not `smartctl -d sat`: a root subprocess per sample and a new package (AGENT.md, SECURITY.md), and it would send the same commands.

## Grok usage

`grok_usage.py` polls the endpoint the official Grok CLI uses for `/usage` (`GET https://cli-chat-proxy.grok.com/v1/billing?format=credits`) every `GROK_USAGE_INTERVAL` seconds over one held connection, with the bearer token from the CLI's login file. State is `creditUsagePercent` (full precision); attributes are `reset` (`currentPeriod.end`), `period` and each `productUsage` share by product name. HA name `Grok Usage` on its own HA device `Grok` (identifier `grok`), global object `grok_usage` and availability `sensor/grok/availability` (no hostname): the subscription is not a host metric. Run it on one host only.

1. Install the Grok CLI ([xai-org/grok-build](https://github.com/xai-org/grok-build)) as the user whose subscription it is.
2. `grok login --device-auth` as that user. It writes `~/.grok/auth.json`.
3. Set `GROK_AUTH_PATH` to that file's absolute path and `GROK_USAGE_INTERVAL` (e.g. `240`) in `.env`.
4. `systemctl enable --now /root/mqtt-sensors/grok_usage.service`

The file is re-read on every poll and never written. The collector does not refresh the token: the CLI's refresh tokens are single-use and rotated under its own lock, so a second refresher would invalidate the CLI's login. When the CLI runs, it refreshes and rewrites the file, and the collector picks that up. When the access token has expired, the endpoint answers 401 and the collector exits with the status and body; systemd keeps restarting it until you run `grok login --device-auth` again.

The access token lasts only hours. `grok models` triggers the CLI's own refresh (under its lock) without a prompt. To run it hourly as the CLI user, copy `user/grok-auth-refresh.service` and `user/grok-auth-refresh.timer` to `~/.config/systemd/user/`, then `systemctl --user enable --now grok-auth-refresh.timer` and `loginctl enable-linger $USER`. If a refresh is rejected, the CLI deletes `auth.json` and `grok login` is needed again.

## Retained messages

Publish is not retained unless there is a clear, commented reason. Discovery configs and availability are never retained: collectors subscribe to `<prefix>/status` and republish them when Home Assistant announces `online`. While a collector is connected, `create_client` also republishes non-retained `online` on its availability topic every 60 s (`AVAILABILITY_HEARTBEAT_SEC` in `mqtt_common.py`). Home Assistant subscribes to an entity's topics only after it has processed the discovery config, so the `online` published right after discovery would be missed and the entity would sit unavailable until the next heartbeat; the heartbeat therefore also repeats `online` 2 s (`DISCOVERY_SETTLE_SEC`) after each `online` the collector publishes (startup, reconnect, HA `online`). Slow non-retained collectors (`support/` pairs and Pearl network) also replay their last states then (`replay_states=True`). After retained availability is cleared (for example by `mqtt_purge_retained.py --delete`), entities recover within that interval without restarting the collectors. Discovery config is still republished only on connect or when HA comes back `online`; if a purge also dropped an entity in HA, it comes back on the next collector reconnect or HA restart. The heartbeat stops when the collector publishes `offline` or calls `disconnect()`, so it cannot overwrite a clean shutdown. A sensor whose collector is gone can be deleted in HA (after any leftover retained messages are cleared).

The exceptions: `kmsg_reset_reason.py` retains config, attributes and state because it is run-once; `xmr_eur_price` and `xmr_per_hs_day` retain state so `xmrig_status` can read them at startup (same 60 s interval for the other retained `xmr_network` states: difficulty and block reward); the Monero block-time entities are not retained; `pearl_usdt_price`, `usdt_eur_price` and `pearl_network` are not retained (nothing reads them at startup; HA shows a value within 60 s); `grok_usage` and `hwmon_drive_temperature` retain state (and grok attributes) because they publish at 60 s or slower. Package power is published every second and is not retained. `ELECTRICITY_EUR_PER_KWH_TOPIC`, when used, is an external topic this repo does not publish.

Retained messages from older versions stay on the broker until cleared. `mqtt_purge_retained.py` lists them (dry run) for this host; `--delete` clears them with zero-length retained payloads, `--all` covers every retained topic on the broker (other integrations too, so check the list first), `--hostname NAME` picks another host. `--entity COMPONENT/OBJECT` (repeatable) limits the list to that one entity: its retained topics plus its `config` topic, so `--delete` also removes an entity whose discovery was never retained (Home Assistant keeps non-retained discovery in memory until it restarts, and will not let you delete it in the UI meanwhile). Removing an entity this way also drops its history.

```sh
python3 mqtt_purge_retained.py
python3 mqtt_purge_retained.py --delete
python3 mqtt_purge_retained.py --entity sensor/pearl_network_hashrate --delete
python3 mqtt_purge_retained.py --script support/pearl_network.py
python3 mqtt_purge_retained.py --script support/pearl_network.py --delete
```

`--script PATH` covers every entity and retained topic of a collector that takes the flags below, by running that script with `--list`, `--purge` with `--delete`, or `--purge-stale` with `--delete --stale` (`hwmon_drive_temperature.py`).

Collectors with a defined init phase also sweep their own stale retained topics and remove their retired entities. So far that is the `support/` collectors (`xmr_network`, `xmr_eur_price`, `pearl_network`, `pearl_usdt_price`, `usdt_eur_price`) and the Chia collectors (`chia_farm_size`, `chia_harvester_processing_time`, `chia_recompute_server_processing_time`). Once it has published everything it expects, each one subscribes to `<prefix>/+/+/+` for about 2 s and collects the retained messages whose object id is one of its own ids, or one of them followed by `_…` (`xmr_network` owns `xmr_network`, `xmr_difficulty`, its block-time ids, `xmr_block_reward` and `xmr_per_hs_day`; `xmr_eur_price` owns `xmr_eur`; `pearl_network` likewise its own ids; `pearl_usdt_price` owns `pearl_usdt`; `usdt_eur_price` owns `usdt_eur`; each Chia collector owns exactly its availability id and entity ids, all ending in `_<host>`, so another host's ids never match). Any of those topics it did not publish this run is stale, for example the old retained `xmr_network_hashrate` state.

A collector also lists its retired entities (ones it no longer publishes): `xmr_network` retires `xmr_network_hashrate`, `pearl_network` `pearl_network_hashrate`, `pearl_usdt_price` the old CoinGecko `pearl_eur_price`, and `chia_recompute_server_processing_time` the pre-rename `chia_recompute_*_<host>` ids (`chia_recompute_server`, `chia_recompute_time`, `chia_recompute_fail`, `chia_recompute_server_time`, `chia_recompute_server_fail`). For each, it clears any retained topic under it and publishes an empty `config`, which removes the entity from HA right away even if its discovery was never retained, and drops its history. A rename should keep the history: rename the entity in HA first, then retire the old id.

`MQTT_SWEEP` decides what happens: `dry` (default) prints each stale topic (`sweep dry: <topic>`), each retired entity config (`sweep dry: remove <topic>`) and a count to the journal; `apply` clears them; `off` skips. It runs once per start, and nothing published this run is ever cleared.

When a collector's init is done:
- `support/` collectors and `chia_farm_size`: after the first poll (all their states published).
- `chia_harvester_processing_time`: after the first eligible-plots line in `debug.log` (time and plot count). It follows the log from the end, so until the harvester logs a signage point (about every 10 s while farming) it has published only discovery and availability, and it does not sweep.
- `chia_recompute_server_processing_time`: after the first `took … ms` line in the `chia_recompute_server` journal. If the recompute server is idle, it does not sweep until work arrives.

Every sweep-enabled collector, and `hwmon_drive_temperature.py`, also takes `--list`, `--purge` and `--help`, and then exits without starting its loop or opening its source (API connection, Chia RPC, log, drive). `--help` prints the flags it supports and exits 0; an unknown flag prints the usage and exits 2.
- `--list` (read-only) prints its current entity config topics, its retired entity config topics, and every retained topic in its scope on the broker (marked `(retained)`).
- `--purge` force-deletes all of them: an empty payload on each `config` (removes the entity and its history from HA, retained or not) and a cleared retained message on every retained topic in scope. It ignores `MQTT_SWEEP`. Stop the collector first, or its next publish recreates the entities.

```sh
systemctl stop pearl_network
python3 support/pearl_network.py --list
python3 support/pearl_network.py --purge
```

Run them from the repo directory so `.env` is found (`mqtt_purge_retained.py --script` does that for you).

Deploy: `git pull`, `systemctl restart xmr_network xmr_eur_price pearl_network pearl_usdt_price usdt_eur_price` and, on Chia hosts, `systemctl restart chia_farm_size chia_harvester_processing_time chia_recompute_server_processing_time`. Check `journalctl -u <service>` for the `sweep dry:` lines, then set `MQTT_SWEEP=apply` in `.env` and restart them again if the list looks right. `MQTT_SWEEP` is per host `.env`, so each host is switched on separately.

## Configuration

`.env` in the working directory (or the environment). Empty means unset.

```
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USER=
MQTT_PASS=
MQTT_PREFIX=homeassistant
MQTT_SWEEP=dry

XMRIG_HOST=127.0.0.1
XMRIG_PORT=44444
XMRIG_TOKEN=
XMRIG_POWER_BASELINE_W=
XMRIG_HASHRATE_PLATEAU_HS=
XMRIG_CONTENTION_MODE=

HA_URL=http://127.0.0.1:8123
HA_TOKEN=
NVIDIA_GPU_STATS=
HWMON_SENSORS_EXCLUDE=
HA_ELECTRICITY_ENTITY=sensor.porssisahko_electricity_price
ELECTRICITY_EUR_PER_KWH_TOPIC=
ELECTRICITY_EUR_PER_KWH=

LIGHTDM_SWITCH_MODE=read_only
OLLAMA_SWITCH_MODE=read_only

DRIVE_TEMPERATURE_INTERVAL=60
DRIVE_TEMPERATURE_DEBUG=

GROK_AUTH_PATH=
GROK_USAGE_INTERVAL=240

PEARL_USDT_SLIPPAGE_PCT=0
PEARL_USDT_PRICE_MODE=avg
USDT_EUR_SLIPPAGE_PCT=0
USDT_EUR_PRICE_MODE=avg

LHM_URL=
```

| variable | meaning |
|---|---|
| **MQTT** | |
| `MQTT_HOST`, `MQTT_PORT` | broker (default `localhost:1883`) |
| `MQTT_USER`, `MQTT_PASS` | broker credentials, optional |
| `MQTT_PREFIX` | discovery prefix (default `homeassistant`) |
| `MQTT_SWEEP` | after init: stale retained-topic sweep and retired-entity removal; `dry` (default, print only), `apply` (clear and remove), `off` |
| **XMRig** | |
| `XMRIG_HOST`, `XMRIG_PORT` | XMRig HTTP API (default `127.0.0.1:44444`) |
| `XMRIG_TOKEN` | XMRig HTTP bearer token, optional |
| `XMRIG_POWER_BASELINE_W` | 100%-intensity package power hint (W), used until measured; must be present, may be empty |
| `XMRIG_HASHRATE_PLATEAU_HS` | 100%-intensity 10 s hashrate hint (H/s), used until measured; must be present, may be empty |
| `XMRIG_CONTENTION_MODE` | `control` enables automatic thread reduction; must be present, empty is off |
| **Electricity price** (topic or fixed; HA only when neither is set) | |
| `ELECTRICITY_EUR_PER_KWH_TOPIC` | MQTT topic with €/kWh |
| `ELECTRICITY_EUR_PER_KWH` | fixed €/kWh |
| `HA_TOKEN` | HA long-lived token; enables polling `HA_ELECTRICITY_ENTITY` every 60 s |
| `HA_URL` | HA base URL (default `http://127.0.0.1:8123`) |
| `HA_ELECTRICITY_ENTITY` | HA spot price entity (default `sensor.porssisahko_electricity_price`) |
| **Unit switches** | |
| `LIGHTDM_SWITCH_MODE`, `OLLAMA_SWITCH_MODE` | `read_only` (default) or `control` (start/stop the unit) |
| **Drive temperature** | |
| `DRIVE_TEMPERATURE_INTERVAL` | seconds between reads (default 60) |
| `DRIVE_TEMPERATURE_DEBUG` | `1` prints SG_IO diagnostics |
| **Grok usage** | |
| `GROK_AUTH_PATH` | absolute path to the Grok CLI's `auth.json` (`~/.grok/auth.json` of the user who ran `grok login`); required by `grok_usage.py` |
| `GROK_USAGE_INTERVAL` | seconds between polls; required by `grok_usage.py`. Keep it under the endpoint's idle timeout (dropped between 250 and 290 s in testing), or every poll hits a closed connection and the collector exits |
| **Market pairs** | |
| `PEARL_USDT_SLIPPAGE_PCT`, `USDT_EUR_SLIPPAGE_PCT` | expected slippage (%) for that conversion step, published as the pair's `slippage_pct` attribute; required by `support/pearl_usdt_price.py` / `support/usdt_eur_price.py` (`0` for none) |
| `PEARL_USDT_PRICE_MODE`, `USDT_EUR_PRICE_MODE` | how the pair's `Price` combines its sources: `avg` (default), `max` or `min`; anything else exits |
| **Windows** | |
| `LHM_URL` | LibreHardwareMonitor web server origin, required by `windows/cpu_package_power.py` |

## Run

As root, from `/root/mqtt-sensors`. Distro packages only: `python3` and `python3-paho-mqtt`. Each collector has a matching `*.service` (`WorkingDirectory=/root/mqtt-sensors`, `Restart=on-failure`, `RestartSec=10`, `WantedBy=default.target`); enable the ones you want as system units:

```
systemctl enable --now /root/mqtt-sensors/xmrig_status.service
```

Upgrading a host: `cd /root/mqtt-sensors && git pull`, add any new `.env` keys from the example above (`XMRIG_POWER_BASELINE_W`, `XMRIG_HASHRATE_PLATEAU_HS` and `XMRIG_CONTENTION_MODE` are read directly and must be present, empty is fine), then restart the enabled units, e.g. `systemctl restart xmrig_status lightdm_active`.
