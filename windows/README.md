# Windows

Linux collectors at the repo root are the reference for what we publish. These scripts copy that contract and do not share the read path. Shared code is `mqtt_common.py` only (dotenv, hostname, discovery, client).

Sensor object ids match Linux: `cpu_package_power_<hostname>`, `gpu<N>_power_<hostname>`, plus the matching `_energy` sensors. Availability topics match too (`cpu_package_power_<hostname>`, `gpu_power_<hostname>`). A Windows miner therefore feeds the same MQTT names as a Linux one, including XMRig profitability’s package-power topic.

The HA device id is `windows_host_<hostname>`, not `linux_host_<hostname>`, so a Windows box does not claim a Linux device. The display name is still the hostname.

This folder is the relaxed exception. An extra program already on the machine is allowed. No vendored driver, no pip, no OS branches in the Linux scripts. Fail-fast still applies: if the source is missing, the process exits. There are no systemd units; keep them running however you already keep a Windows process up (Task Scheduler is enough).

Run from anywhere. The scripts `chdir` to the repo root so `.env` loads the same way as the Linux units (`WorkingDirectory`).

## CPU

`cpu_package_power.py` holds one `powershell.exe` process that prints LibreHardwareMonitor’s WMI sensor `CPU Package` (watts) once a second. Energy is watts × dt in RAM, published as kWh, same units as the RAPL collector.

Prerequisite: LibreHardwareMonitor running with the WMI provider enabled (`root/LibreHardwareMonitor`, sensor name `CPU Package`). Not PDH. Not an MSR driver in this repo.

```
python windows/cpu_package_power.py
```

## GPU

`nvidia_gpu_power.py` uses the same `nvidia-smi --query-gpu=index,name,power.draw --format=csv,noheader,nounits --loop=1` query as Linux. It does not use `select()` (that does not work on Windows pipes). A one-shot query counts GPUs; each sample is that many lines.

Prerequisite: NVIDIA driver, `nvidia-smi` on `PATH`.

```
python windows/nvidia_gpu_power.py
```
