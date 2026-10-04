#!/usr/bin/env python3
"""Windows CPU package power + energy → MQTT + HA (LibreHardwareMonitor HTTP).

Linux reference: cpu_package_power.py. Same object ids and units. Read path is not shared.
"""
import json, os, re, subprocess, sys, time, http.client
from pathlib import Path
from urllib.parse import urlparse
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
LHM = urlparse(os.environ["LHM_URL"])
if LHM.scheme != "http" or not LHM.hostname or LHM.port is None:
    raise SystemExit("LHM_URL must be http://host:port")
STATE_POWER = f"{PREFIX}/sensor/{OBJECT}/state"
STATE_ENERGY = f"{PREFIX}/sensor/{OBJECT}_energy/state"
CONFIG_POWER = f"{PREFIX}/sensor/{OBJECT}/config"
CONFIG_ENERGY = f"{PREFIX}/sensor/{OBJECT}_energy/config"
AVAIL_T = f"{PREFIX}/sensor/{OBJECT}/availability"

def hidden_console():
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info

def cpu_model():
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
        startupinfo=hidden_console(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    model = re.sub(r"\s+", " ", (proc.stdout or "").strip())
    model = re.sub(r"\(R\)|\(TM\)|CPU @.*|with Radeon.*", "", model, flags=re.I).strip()
    return model or "CPU"

def on_cpu(hardware_id):
    part = hardware_id.split("/")
    return len(part) > 1 and part[1].endswith("cpu")

def find_package(node, hardware_id=""):
    hardware_id = node.get("HardwareId") or hardware_id
    text = node.get("Text")
    if node.get("Type") == "Power" and (text == "CPU Package" or (text == "Package" and on_cpu(hardware_id))):
        return node
    for child in node["Children"]:
        found = find_package(child, hardware_id)
        if found is not None:
            return found
    return None

def parse_watts(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit(f"LibreHardwareMonitor CPU Package value unreadable: {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    match = re.match(r"\s*([+-]?[0-9][0-9.,]*)", value)
    if not match:
        raise SystemExit(f"LibreHardwareMonitor CPU Package value unreadable: {value!r}")
    num = match.group(1)
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        num = num.replace(",", ".")
    try:
        return float(num)
    except ValueError:
        raise SystemExit(f"LibreHardwareMonitor CPU Package value unreadable: {value!r}")

def read_package_watts():
    conn.request("GET", "/data.json")
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise SystemExit(f"LibreHardwareMonitor HTTP {resp.status}")
    sensor = find_package(json.loads(body))
    if sensor is None:
        raise SystemExit("LibreHardwareMonitor CPU Package sensor missing")
    return parse_watts(sensor["RawValue"])

CPU_MODEL = cpu_model()
DISCOVERY_POWER = make_power_discovery(
    f"{CPU_MODEL} Package Power", STATE_POWER, AVAIL_T, OBJECT, DEVICE
)
DISCOVERY_ENERGY = make_energy_discovery(
    f"{CPU_MODEL} Package Energy", STATE_ENERGY, AVAIL_T, f"{OBJECT}_energy", DEVICE
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

energy_wh = 0.0
t_prev = time.monotonic()
conn = http.client.HTTPConnection(LHM.hostname, LHM.port, timeout=5)

def stop():
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    conn.close()

try:
    conn.connect()
    while True:
        power = read_package_watts()
        t_now = time.monotonic()
        dt = max(t_now - t_prev, 0.001)
        t_prev = t_now
        energy_wh += power * (dt / 3600.0)
        client.publish(STATE_POWER, f"{power:.1f}", retain=True)
        client.publish(STATE_ENERGY, f"{energy_wh / 1000.0:.6f}", retain=True)
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    stop()
