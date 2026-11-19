# Windows

The Linux collectors at the repo root define what is published; these scripts copy that contract with Windows sources. Shared code is in `lib/` (`mqtt_common`, `nvidia_common`, `board_common`, `xmrig_common`), including the no-retain rule and the availability heartbeat. Object ids, topics, availability and entity ids follow the same [identity rules](../README.md#identity) as Linux (`<slot>_cpu_package_power`, `<slot>_xmrig_*`, `<slot>_gpu<N>_power`, `<slot>_gpu<N>_energy`, GPU stats `gpu_<uuid>_<Model>_<metric>` on the GPU's own device), so a Windows miner feeds the same MQTT names. `<slot>` is `MACHINE_ID` from the repo-root `.env` as written, else the hostname as-is. The HA device is `windows_host_<slot>` named `<slot>` with underscores as spaces (`Olohuone PC1`), hostname as model when it differs; entity ids are the lowercase form. Each script also takes `--help`, `--list`, `--purge-stale` (safe while it runs) and `--purge` (`python windows\<script>.py --list`).

Relaxed exception: a program already on the machine may be a source. No vendored driver, no pip. Fail-fast still applies.

## Run

Double-click `windows/tray.pyw` (no console). Configuration is the repo-root `.env` (see the [root README](../README.md#configuration)). The menu opens the repo-root `.env` and exits if that file is missing. The scripts `chdir` to the repo root.

- The menu lists every collector here; a tick enables it and keeps it running (short restart backoff, like `Restart=on-failure`), untick stops it, quit stops what the tray started.
- ✓ on a label: running now. Icon `tray_warn.ico`: an enabled collector is down.
- Output appends to `windows/logs/<name>.log`; ticks are kept in `windows/tray_enabled.txt`.

Upgrading: `git pull`, add any new `.env` keys from the root example, then quit and restart the tray.

## CPU

`cpu_package_power.py` polls LibreHardwareMonitor's web server (`GET /data.json` on `LHM_URL`, required) once a second. Power is the `CPU Package` sensor, or `Package` on AMD (`RawValue`, W); energy is integrated in RAM as kWh. Needs LibreHardwareMonitor running with the remote web server enabled.

## GPU

`nvidia_gpu_power.py` runs the same `nvidia-smi --query-gpu=index,name,power.draw --format=csv,noheader,nounits --loop=1` as Linux, without `select()` (no Windows pipe support). A one-shot query counts GPUs. Needs the NVIDIA driver and `nvidia-smi` on `PATH`.

## Hardware sensors

`lhm_sensors.py` reads the same `GET /data.json` (`LHM_URL`) every 5 s on one held connection and publishes every Temperature, Fan, Voltage, Clock, Load and Control sensor as `mb_<board>_lhm_<SensorId>` (`<board>` from `Win32_BaseBoard.SerialNumber`, else `Win32_ComputerSystemProduct.UUID`, else manufacturer + product; one PowerShell query at start) (includes GPU Hot Spot / Memory Junction when LHM shows them). `nvidia_gpu_power.py` adds the Linux GPU stats when `NVIDIA_GPU_STATS` is non-empty.

LHM sensor IDs include the GPU index, so identical GPU models and sensor names keep separate MQTT entities; with the board prefix they follow the board and slot index, not the card (the `NVIDIA_GPU_STATS` entities of `nvidia_gpu_power.py` follow the card UUID; power and energy stay on the slot). Missing, null, non-numeric and non-finite readings are skipped per sensor (including unavailable sensors on an idle GPU); valid readings from the other sensors continue publishing, and a sensor resumes when its reading returns.

```
python windows/lhm_sensors.py
```

## XMRig

`xmrig_status.py` matches the Linux collector (switch, intensity, reject ratio, profitability, mining power attribution; see the root README) on one held HTTP connection. The switch and intensity need `http.restricted=false`.

Baseline, plateau, hints and contention handling are shared with Linux (`MiningIntensity` and `Contention` in `lib/xmrig_common.py`); see the [root README](../README.md#xmrig).
