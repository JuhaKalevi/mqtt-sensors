# mqtt-sensors

Long-lived root processes that publish host stats to MQTT with Home Assistant discovery. One process per collector; each holds open a source or polls one in-process. No framework, no `requirements.txt`, no pip.

Collectors on a host share one HA device named after the hostname (`linux_host_<hostname>`), except device-scoped power-supply batteries, which get their own HA device linked with `via_device`. Screenshots: farmer host `M710q` (CPU RAPL + farm size), and miner host `B850Pro` (package/GPU power, XMRig hashrate/switch/reject + profitability factor). Recompute and harvester processing times land on whichever machine actually runs those processes.

![Home Assistant — M710q](screen.png)

![Home Assistant — B850Pro](screen2.png)

[SECURITY.md](SECURITY.md) is the supply-chain / root policy. [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) is fail-fast / minimalism. [AGENT.md](AGENT.md) is the contract for coding agents adding a sensor — same role as an `AGENTS.md`. You can ignore it. Windows CPU/GPU copies of the Linux power collectors live in [windows/](windows/README.md).

## Collectors

| collector | what HA shows | source (held open) |
|---|---|---|
| `cpu_package_power.py` | package power (W) and energy (kWh, RAM only, resets on start) | RAPL `energy_uj` fd |
| `nvidia_gpu_power.py` | per-GPU power and energy | one `nvidia-smi --loop=1` |
| `windows/cpu_package_power.py` | package power (W) and energy (kWh, RAM only) | LibreHardwareMonitor WMI `CPU Package` via one PowerShell loop |
| `windows/nvidia_gpu_power.py` | per-GPU power and energy | one `nvidia-smi --loop=1` (no `select`) |
| `chia_farm_size.py` | plots, on-disk TiB, effective TiB, estimated netspace EiB, ETA to win (s) | farmer `:8559` + full node `:8555` TLS |
| `chia_recompute_server_processing_time.py` | recompute processing time (s, full precision, display 1 decimal) | `journalctl -u chia_recompute_server -f` |
| `chia_harvester_processing_time.py` | harvester processing time (s, full precision, display 1 decimal) and plot count | uid 1000 `debug.log` fd, reopen on daily rotate |
| `loginctl_active_users.py` | comma-separated users with `active` or `online` logind sessions | `/run/systemd/users` (what `loginctl` reads; no spawn) |
| `lightdm_active.py` | switch: LightDM unit running; start/stop only with `LIGHTDM_SWITCH_MODE=control` | `/sys/fs/cgroup/system.slice/lightdm.service` (no `systemctl` to read state) |
| `hwmon_drive_temperature.py` | per-drive temperature (°C): `drivetemp`/`nvme` hwmon, else in-process SAT (SG_IO) SMART for `sd*` incl. USB; drives reporting standby are not read | `/sys/class/hwmon` polled every 60 s (`DRIVE_TEMPERATURE_INTERVAL`); `temp1_input` fd held while the node exists |
| `power_supply_battery.py` | per-battery capacity (%) | `/sys/class/power_supply` polled each second; `capacity` fd held while the node exists |
| `xmrig_status.py` | hashrate (H/s, 60s), reject ratio (%), mining switch, profitability factor (revenue/cost) | XMRig HTTP + MQTT inputs; remembers hashrate/W while paused |
| `support/xmr_eur_price.py` | XMR/EUR spot (EUR) | held HTTPS to Kraken public ticker, 60 s |
| `support/xmr_network.py` | Monero difficulty, network hashrate (H/s), block reward (XMR), XMR/H/s/day | held HTTPS to xmrchain.net, 60 s |

Run only the collectors that apply. GPU is a no-op without `nvidia-smi`. Farm needs a local farmer and full node. Recompute needs `chia_recompute_server` in the journal. Harvester follows `plots were eligible for farming … Time: N s. Total N plots` in `debug.log` (via `chia_root()`). Chia paths are uid 1000's `.chia/mainnet`, never `/root` and never a username. Power supply batteries are any sysfs node with `type=Battery` and a `capacity` file (Logitech `hidpp_battery_*`, laptop `BAT*`, and the like). The collector rescans that class dir each second so plug/unplug is picked up without a restart, and clears retained discovery when a node disappears. `scope=System` (laptop/UPS) stays on the host HA device; other scopes get their own HA device via the host so a mouse battery does not claim the host device battery badge. XMRig needs its HTTP API on localhost (`XMRIG_HOST`/`XMRIG_PORT`/`XMRIG_TOKEN` in `.env`); the mining switch requires `http.restricted=false` (XMRig is read-only or full write — there is no pause-only ACL). Reject ratio is `(shares_total - shares_good) / shares_total`. Without a spot €/kWh source, `xmrig_status` stays at basic mining stats. Default spot source is Home Assistant entity `sensor.porssisahko_electricity_price` (override with `HA_ELECTRICITY_ENTITY`) when `HA_TOKEN` is set (`HA_URL` defaults to `http://127.0.0.1:8123`, polled every 60 s). Alternatives: `ELECTRICITY_EUR_PER_KWH_TOPIC` (MQTT) or fixed `ELECTRICITY_EUR_PER_KWH`. It also waits for retained MQTT from package power, `xmr_per_hs_day`, and XMR/EUR before publishing the factor. Factor is `revenue/cost` (1.0 ≈ break-even). Publish-only — no default automation. While paused, last mining hashrate and package watts stay in RAM. `support/` holds helpers that are not host metrics (global MQTT object ids, no hostname): `xmr_eur_price.py` publishes Kraken’s XMR/EUR last trade; `xmr_network.py` publishes Monero difficulty, network hashrate, last-block reward, and XMR per H/s per day (`reward * 86400 / difficulty`) for hashrate profitability. Expected XMR/day ≈ local hashrate × that rate.

Energy is `total_increasing` kWh integrated in RAM; HA expects it to start at 0. ETA is `(netspace / effective) * 18.75`. Recompute work arrives in 10 s bursts; 0.2 s–5 s is normal — do not average. Harvester samples once per signage point (~every 9 s); a gap or a time climbing toward the signage window is the error signal.

Topics: `$MQTT_PREFIX/sensor/<object>/{config,state}` (default prefix `homeassistant`). Binary sensors use `binary_sensor/<object>/…`. Availability is per collector: `gpu_power_<host>`, `chia_farm_<host>`, `chia_recompute_server_<host>`, `chia_harvester_<host>`, `loginctl_<host>`, `lightdm_<host>`, `power_supply_<host>`, `hwmon_drive_temperature_<host>`, `xmrig_<host>`, `xmr_eur_<host>`, `xmr_network_<host>`. Switches use `$MQTT_PREFIX/switch/<object>/{config,state,set}`.

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

## LightDM switch

`lightdm_active.py` publishes a HA `switch` (`switch/lightdm_active_<host>/{config,state,set}`). State is the `lightdm.service` cgroup dir existing, checked each second. On connect it clears the retained discovery and state of the old `binary_sensor/lightdm_active_<host>` so HA drops that entity.

`LIGHTDM_SWITCH_MODE` in `.env`:

- `read_only` (default, also when unset or empty): HA requires a `command_topic`, so the switch has one, but commands are ignored (printed) and the real state is re-published, so the toggle snaps back.
- `control`: `ON` runs `systemctl start lightdm.service`, `OFF` runs `systemctl stop lightdm.service`, prints the action and exit code, then re-publishes the real state. The collector runs as root, so no sudoers or polkit rule is needed. `OFF` ends any graphical session on that seat.

Any other value exits.

## Drive temperature

On USB bridges that cannot report the drive's power state, this collector reads the drive every interval and may keep it from spinning down. If that matters, raise `DRIVE_TEMPERATURE_INTERVAL` or do not run the collector on that host.

`hwmon_drive_temperature.py` runs no subprocess (no `smartctl`). Two sources, one `°C` / `temperature` sensor per drive on the host HA device:

1. **hwmon:** `temp1_input` (millidegrees C) of every `/sys/class/hwmon/hwmon*` named `drivetemp` (SATA, also behind SAS HBAs and some USB bridges) or `nvme`. This is preferred when present.
2. **SAT fallback:** every `/sys/block/sd*` not already covered by a `drivetemp` hwmon (same SCSI device path), not `removable`, with non-zero size. The collector holds `/dev/sdX` open (`O_RDONLY|O_NONBLOCK`) and sends read-only ATA commands in-process with the `SG_IO` ioctl (stdlib `fcntl` + `ctypes`) wrapped in SCSI ATA PASS-THROUGH(16), falling back to (12). This works without `drivetemp` and on `usb-storage` bridges where `drivetemp` never attaches. Needs root (`CAP_SYS_RAWIO`), which every collector here has.

SAT per sample: CHECK POWER MODE (`E5h`, non-data, `CK_COND` so the bridge returns the ATA registers; CDB flags byte `2Ch` as smartmontools sends it). If it answers standby (`00h`/`01h`) or another non-active value, nothing more is sent and nothing is published; HA keeps the last value. If it answers active/idle (`FFh`, `80h`–`83h`, `41h`), or the bridge cannot answer at all (error, no or invalid registers), the collector goes on: IDENTIFY DEVICE (`ECh`) once per appearance for the real ATA serial/model and the SMART support/enabled bits, then SMART READ DATA (`B0h`/`D0h`) every sample, publishing raw byte 0 of attribute 194, else 190, if 1–99 °C.

Ids: the ATA serial (and full ATA model) from `vpd_pg89` for `drivetemp`, or from IDENTIFY for SAT, so both paths give the same id. Then the SCSI `serial` / `vpd_pg80`, `wwid`, then for USB the enclosure's `idVendor` + `idProduct` + `serial` + LUN. Nothing stable: skipped; `sdX` is never used. NVMe uses the controller's `model` + `serial`. Object and unique_id are `hwmon_drive_temperature_<id>_<host>`. USB drives get ` (USB)` in the HA name. `/sys/class/hwmon` and `/sys/block` are rescanned each sample (`device` links re-checked), so drives come and go, and a reused `hwmonN` / `sdX` is a new drive. A failed read skips that sample for that drive only. It exits only if there is no `drivetemp`/`nvme` hwmon and no candidate `sd*` disk.

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

When a USB SAT temperature read fails while the block device uses `uas`, the collector logs one suggestion per VID:PID per process start, for example:

```
usb-storage quirk suggested: 174c:55aa:u
```

The collector only reads sysfs and does not apply the quirk. An administrator can apply it with a modprobe configuration such as `options usb-storage quirks=174c:55aa:u`, or with `usb-storage.quirks=174c:55aa:u` on the kernel command line when `usb-storage` is built in. It takes effect on the next probe (replug or reboot), applies to every enclosure with that VID:PID, and `/dev/sdX` names may change.

Why not `smartctl -d sat`: a root subprocess per sample and a new package (AGENT.md, SECURITY.md), and it would send the same commands.

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
HA_URL=http://127.0.0.1:8123
HA_TOKEN=
HA_ELECTRICITY_ENTITY=sensor.porssisahko_electricity_price
ELECTRICITY_EUR_PER_KWH_TOPIC=
ELECTRICITY_EUR_PER_KWH=
LIGHTDM_SWITCH_MODE=read_only
DRIVE_TEMPERATURE_INTERVAL=60
DRIVE_TEMPERATURE_DEBUG=
```

Matching `*.service` stubs: `ExecStart`, `WorkingDirectory=/root/mqtt-sensors`, `Restart=on-failure`, `RestartSec=10`, `WantedBy=default.target`. Enable the ones you want as system units.
