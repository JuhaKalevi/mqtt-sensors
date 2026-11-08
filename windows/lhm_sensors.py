#!/usr/bin/env python3
"""Windows hardware temperatures, fans, voltages, clocks and loads → MQTT + HA (LibreHardwareMonitor HTTP, 5 s).

Linux reference: hwmon_sensors.py. Read path is not shared.
"""
import json, math, os, re, sys, time, http.client
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])
from mqtt_common import get_hostname, get_mqtt_settings, make_sensor_discovery, create_client

HOSTNAME = get_hostname()
DEVICE = {"identifiers": [f"windows_host_{HOSTNAME}"], "name": HOSTNAME}
CLIENT_ID = f"lhm-sensors-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
LHM = urlparse(os.environ["LHM_URL"])
if LHM.scheme != "http" or not LHM.hostname or LHM.port is None:
    raise SystemExit("LHM_URL must be http://host:port")
AVAIL_T = f"{PREFIX}/sensor/lhm_sensors_{HOSTNAME}/availability"
KINDS = {
    "Temperature": ("°C", "temperature", 1),
    "Fan": ("RPM", None, 0),
    "Voltage": ("V", "voltage", 3),
    "Clock": ("MHz", "frequency", 0),
    "Load": ("%", None, 1),
    "Control": ("%", None, 0),
}

def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

def parse_value(value):
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    match = re.match(r"\s*([+-]?[0-9][0-9.,]*)", value) if isinstance(value, str) else None
    if not match:
        return None
    num = match.group(1)
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        num = num.replace(",", ".")
    try:
        number = float(num)
    except ValueError:
        return None
    return number if math.isfinite(number) else None

def walk(node, hardware, out):
    if node.get("HardwareId"):
        hardware = node["Text"]
    if node.get("Type") in KINDS and node.get("SensorId"):
        out.append((node["SensorId"], hardware, node["Text"], node["Type"], node.get("RawValue")))
    for child in node["Children"]:
        walk(child, hardware, out)
    return out

def read_all():
    conn.request("GET", "/data.json")
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise SystemExit(f"LibreHardwareMonitor HTTP {resp.status}")
    return walk(json.loads(body), "", [])

conn = http.client.HTTPConnection(LHM.hostname, LHM.port, timeout=5)
conn.connect()
sensors = {}
for sid, hardware, text, kind, _ in read_all():
    unit, dclass, prec = KINDS[kind]
    unique = f"lhm_{slug(sid)}_{HOSTNAME}"
    state = f"{PREFIX}/sensor/{unique}/state"
    sensors[sid] = {
        "fan": kind == "Fan", "state": state,
        "config": f"{PREFIX}/sensor/{unique}/config",
        "disc": make_sensor_discovery(f"{hardware} {text} {kind}", state, AVAIL_T, unique, DEVICE,
                                      unit, dclass, "measurement") | {"suggested_display_precision": prec},
    }
if not sensors:
    raise SystemExit("LibreHardwareMonitor reported no sensors")

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        for s in sensors.values():
            client.publish(s["config"], json.dumps(s["disc"]), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

def stop():
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    conn.close()

try:
    while True:
        for sid, _, _, _, raw in read_all():
            s = sensors.get(sid)
            if s:
                value = parse_value(raw)
                if value is not None:
                    client.publish(s["state"], str(round(value) if s["fan"] else value), retain=True)
        time.sleep(5.0)
except KeyboardInterrupt:
    pass
finally:
    stop()
