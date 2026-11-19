#!/usr/bin/env python3
"""Windows hardware temperatures, fans, voltages, clocks and loads → MQTT + HA (LibreHardwareMonitor HTTP, 5 s).

Linux reference: hwmon_sensors.py. Read path is not shared.
"""
import functools, json, math, os, re, subprocess, sys, time, http.client
from pathlib import Path
from urllib.parse import urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client,
    handle_cli, slot_id
)
from lib.board_common import pick_board_id

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device("windows")
CLIENT_ID = f"lhm-sensors-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/{slot_id('lhm_sensors')}/availability"
KINDS = {
    "Temperature": ("°C", "temperature", 1),
    "Fan": ("RPM", None, 0),
    "Voltage": ("V", "voltage", 3),
    "Clock": ("MHz", "frequency", 0),
    "Load": ("%", None, 1),
    "Control": ("%", None, 0),
}

MAIN_HARDWARE = re.compile(r"/(amdcpu|intelcpu|gpu-[a-z]+)/")

def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

@functools.cache
def board_id():
    """Win32_BaseBoard serial, else Win32_ComputerSystemProduct UUID, else manufacturer + product (one PowerShell query)."""
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command",
         "$b = Get-CimInstance Win32_BaseBoard | Select-Object -First 1; "
         "$p = Get-CimInstance Win32_ComputerSystemProduct | Select-Object -First 1; "
         "@($b.SerialNumber, $p.UUID, $b.Manufacturer, $b.Product) -join [char]10"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, startupinfo=info,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    lines = ((proc.stdout or "").splitlines() + ["", "", "", ""])[:4]
    return pick_board_id(*(line.strip() for line in lines))

def unique_of(sid):
    """Motherboard-keyed, like hwmon_sensors.py: LHM sensor ids repeat across machines."""
    return f"mb_{board_id()}_lhm_{slug(sid)}"

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

@functools.cache
def lhm_conn():
    """LHM_URL is read here, not at import, so --help works without it (--list/--purge/--claim need it)."""
    lhm = urlparse(os.environ["LHM_URL"])
    if lhm.scheme != "http" or not lhm.hostname or lhm.port is None:
        raise SystemExit("LHM_URL must be http://host:port")
    return http.client.HTTPConnection(lhm.hostname, lhm.port, timeout=5)

def read_all():
    conn = lhm_conn()
    conn.request("GET", "/data.json")
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise SystemExit(f"LibreHardwareMonitor HTTP {resp.status}")
    return walk(json.loads(body), "", [])

handle_cli(
    CLIENT_ID, settings, owned=(slot_id("lhm_sensors"),),
    entities=lambda: tuple(f"sensor/{unique_of(sid)}" for sid, *_ in read_all()),
)
conn = lhm_conn()
conn.connect()
sensors = {}
for sid, hardware, text, kind, _ in read_all():
    unit, dclass, prec = KINDS[kind]
    unique = unique_of(sid)
    state = f"{PREFIX}/sensor/{unique}/state"
    sensors[sid] = {
        "fan": kind == "Fan", "state": state,
        "config": f"{PREFIX}/sensor/{unique}/config",
        "disc": make_sensor_discovery(f"{hardware} {text} {kind}", state, AVAIL_T, unique, DEVICE,
                                      unit, dclass, "measurement") | {"suggested_display_precision": prec}
                | ({} if kind == "Temperature" and MAIN_HARDWARE.match(sid) else {"entity_category": "diagnostic"}),
    }
if not sensors:
    raise SystemExit("LibreHardwareMonitor reported no sensors")

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        for s in sensors.values():
            client.publish(s["config"], json.dumps(s["disc"]))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

def stop():
    client.publish(AVAIL_T, "offline")
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
                    client.publish(s["state"], str(round(value) if s["fan"] else value))
        time.sleep(5.0)
except KeyboardInterrupt:
    pass
finally:
    stop()
