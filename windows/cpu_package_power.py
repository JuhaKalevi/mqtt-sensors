#!/usr/bin/env python3
"""Windows CPU package power + energy → MQTT + HA (LibreHardwareMonitor WMI).

Linux reference: cpu_package_power.py. Same object ids and units. Read path is not shared.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])
from mqtt_common import (
    get_hostname, get_mqtt_settings,
    make_power_discovery, make_energy_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE = {"identifiers": [f"windows_host_{HOSTNAME}"], "name": HOSTNAME}
OBJECT = f"cpu_package_power_{HOSTNAME}"
CLIENT_ID = f"cpu-power-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
STATE_POWER = f"{PREFIX}/sensor/{OBJECT}/state"
STATE_ENERGY = f"{PREFIX}/sensor/{OBJECT}_energy/state"
CONFIG_POWER = f"{PREFIX}/sensor/{OBJECT}/config"
CONFIG_ENERGY = f"{PREFIX}/sensor/{OBJECT}_energy/config"
AVAIL_T = f"{PREFIX}/sensor/{OBJECT}/availability"

def cpu_model():
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"],
        capture_output=True, text=True,
    )
    model = re.sub(r"\s+", " ", (proc.stdout or "").strip())
    model = re.sub(r"\(R\)|\(TM\)|CPU @.*|with Radeon.*", "", model, flags=re.I).strip()
    return model or "CPU"

CPU_MODEL = cpu_model()
DISCOVERY_POWER = make_power_discovery(
    f"{CPU_MODEL} Package Power", STATE_POWER, AVAIL_T, OBJECT, DEVICE
)
DISCOVERY_ENERGY = make_energy_discovery(
    f"{CPU_MODEL} Package Energy", STATE_ENERGY, AVAIL_T, f"{OBJECT}_energy", DEVICE
)

PS = (
    "$ErrorActionPreference = 'Stop'\n"
    "while ($true) {\n"
    "  $s = Get-CimInstance -Namespace root/LibreHardwareMonitor -ClassName Sensor |\n"
    "    Where-Object { $_.SensorType -eq 'Power' -and $_.Name -eq 'CPU Package' } |\n"
    "    Select-Object -First 1\n"
    "  if (-not $s) { exit 1 }\n"
    "  Write-Output ([string]$s.Value)\n"
    "  Start-Sleep -Seconds 1\n"
    "}\n"
)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_POWER, json.dumps(DISCOVERY_POWER), retain=True)
        client.publish(CONFIG_ENERGY, json.dumps(DISCOVERY_ENERGY), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

proc = subprocess.Popen(
    ["powershell.exe", "-NoProfile", "-Command", PS],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    bufsize=1,
)
energy_wh = 0.0
t_prev = time.monotonic()

def stop():
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    if proc.poll() is None:
        proc.terminate()

try:
    while True:
        line = proc.stdout.readline()
        if not line:
            raise SystemExit("LibreHardwareMonitor CPU Package sensor ended")
        line = line.strip()
        if not line:
            continue
        power = float(line)
        t_now = time.monotonic()
        dt = max(t_now - t_prev, 0.001)
        t_prev = t_now
        energy_wh += power * (dt / 3600.0)
        client.publish(STATE_POWER, f"{power:.1f}", retain=True)
        client.publish(STATE_ENERGY, f"{energy_wh / 1000.0:.6f}", retain=True)
except KeyboardInterrupt:
    pass
finally:
    stop()
