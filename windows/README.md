# Windows

The Linux collectors at the repo root define what is published; these scripts copy that contract with Windows sources. Shared code is `mqtt_common.py` only. Object ids, topics and availability match Linux (`cpu_package_power_<hostname>`, `gpu<N>_power_<hostname>`, `xmrig_*_<hostname>`), so a Windows miner feeds the same MQTT names. The HA device id is `windows_host_<hostname>` (display name is the hostname).

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

## XMRig

`xmrig_status.py` matches the Linux collector (switch, intensity, reject ratio, profitability, mining power attribution; see the root README) on one held HTTP connection. The switch and intensity need `http.restricted=false`.

Baseline and plateau: the first ~5 min at 100% intensity set the power baseline (max package W) and the hashrate plateau (max 10 s hashrate). `XMRIG_POWER_BASELINE_W` and `XMRIG_HASHRATE_PLATEAU_HS` act as those values from the start until the 5 min are measured, so a game started right after boot does not skew them. Afterwards both follow the ±5% drift check every ~5 min against the rolling ~15 min max.

Contention handling, with `XMRIG_CONTENTION_MODE=control` and once both values exist (`f = threads/max_threads`):

- **Detect:** package power at least 90% of `f * baseline` while 10 s hashrate / plateau is below `0.8 * f`, for 15 s.
- **Drop:** threads go to `round(relative hashrate * max_threads)` (at least one fewer), re-checked every 10 s; settles within about a minute.
- **Recover** (only after an automatic drop): 5 min uncontested adds one thread and sets a fast-rampup flag; while it holds, each uncontested minute adds another until 100%. Contention clears the flag and the next recovery waits 5 min again.
- A slider command cancels the automatic state. The slider always shows the threads XMRig really runs.
