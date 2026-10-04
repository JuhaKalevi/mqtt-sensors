# Windows

Linux collectors at the repo root are the reference for what we publish. These scripts copy that contract and do not share the read path. Shared code is `mqtt_common.py` only (dotenv, hostname, discovery, client).

Sensor object ids match Linux: `cpu_package_power_<hostname>`, `gpu<N>_power_<hostname>`, plus the matching `_energy` sensors, and `xmrig_hashrate_<hostname>`, `xmrig_reject_ratio_<hostname>`, `xmrig_mining_<hostname>`, `xmrig_profitability_factor_<hostname>`. Availability topics match too (`cpu_package_power_<hostname>`, `gpu_power_<hostname>`, `xmrig_<hostname>`). A Windows miner therefore feeds the same MQTT names as a Linux one, including XMRig profitability’s package-power topic.

The HA device id is `windows_host_<hostname>`, not `linux_host_<hostname>`, so a Windows box does not claim a Linux device. The display name is still the hostname.

This folder is the relaxed exception. An extra program already on the machine is allowed. No vendored driver, no pip, no OS branches in the Linux scripts. Fail-fast still applies: if the source is missing, the process exits. There are no systemd units. Double-click `windows/tray.pyw` (no console). The menu lists every collector script here except the tray; a tick runs it with no window, untick stops that process, and quit stops the ones the tray started. Ticks are remembered in `windows/tray_enabled.txt`. The menu opens the repo-root `.env` and exits if that file is missing.

Run from anywhere. The scripts `chdir` to the repo root so `.env` loads the same way as the Linux units (`WorkingDirectory`).

## CPU

`cpu_package_power.py` polls LibreHardwareMonitor’s HTTP server once a second (`GET /data.json`). `LHM_URL` is the server origin (`http://host:port`) and is required. The Power sensor is `CPU Package`, or `Package` on an AMD CPU (`RawValue`, watts). Energy is watts × dt in RAM, published as kWh, same units as the RAPL collector.

Prerequisite: LibreHardwareMonitor running with the remote web server enabled. Not WMI. Not PDH. Not an MSR driver in this repo.

```
python windows/cpu_package_power.py
```

## GPU

`nvidia_gpu_power.py` uses the same `nvidia-smi --query-gpu=index,name,power.draw --format=csv,noheader,nounits --loop=1` query as Linux. It does not use `select()` (that does not work on Windows pipes). A one-shot query counts GPUs; each sample is that many lines.

Prerequisite: NVIDIA driver, `nvidia-smi` on `PATH`.

```
python windows/nvidia_gpu_power.py
```

## XMRig

`xmrig_status.py` polls XMRig’s HTTP API (`GET /2/summary`, `POST /json_rpc`) on one held connection. No console program. `XMRIG_HOST` and `XMRIG_PORT` default to `127.0.0.1` and `44444`; `XMRIG_TOKEN` is sent when set. Hashrate, reject ratio, and the mining switch match the Linux objects. The profitability factor is published when the same inputs exist (package power, `xmr_per_hs_day`, XMR/EUR, and a spot price). The switch needs `http.restricted=false`. If XMRig is not reachable the process exits.

```
python windows/xmrig_status.py
```
