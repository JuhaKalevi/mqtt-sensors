#!/usr/bin/env python3
"""hwmon temperatures, fans and voltages → MQTT + Home Assistant discovery (held sysfs fds, 5 s)."""
import json, os, re, signal, time
from pathlib import Path
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, _ = make_device(HOSTNAME)
CLIENT_ID = f"hwmon-sensors-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/hwmon_sensors_{HOSTNAME}/availability"
EXCLUDE = {x.strip() for x in os.environ["HWMON_SENSORS_EXCLUDE"].split(",") if x.strip()}
SKIP = {"drivetemp", "nvme", "nouveau"}
KINDS = {
    "temp": ("Temperature", "°C", "temperature", 1000.0, 1),
    "fan": ("Fan Speed", "RPM", None, 1, 0),
    "in": ("Voltage", "V", "voltage", 1000.0, 3),
}

def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

chips = []
for d in sorted(Path("/sys/class/hwmon").iterdir()):
    name = (d / "name").read_text().strip()
    if name not in SKIP:
        chips.append((name, d))
counts = {}
for name, _ in chips:
    counts[name] = counts.get(name, 0) + 1

sensors = []
for name, d in chips:
    chip = name if counts[name] == 1 else f"{name}_{slug(os.path.basename(os.path.realpath(d / 'device')))}"
    for f in sorted(d.glob("*_input")):
        m = re.fullmatch(r"(temp|fan|in)(\d+)_input", f.name)
        if not m:
            continue
        inp = f"{m.group(1)}{m.group(2)}"
        lf = d / f"{inp}_label"
        label = lf.read_text().strip() if lf.exists() else inp
        if f"{chip}/{label}" in EXCLUDE:
            continue
        fd = os.open(f, os.O_RDONLY)
        try:
            int(os.pread(fd, 32, 0))
        except OSError:
            os.close(fd)
            continue
        kind, unit, dclass, scale, prec = KINDS[m.group(1)]
        unique = f"hwmon_{slug(chip)}_{inp}_{HOSTNAME}"
        state = f"{PREFIX}/sensor/{unique}/state"
        sensors.append({
            "fd": fd, "scale": scale, "state": state,
            "config": f"{PREFIX}/sensor/{unique}/config",
            "disc": make_sensor_discovery(f"{chip} {label} {kind}", state, AVAIL_T, unique, DEVICE,
                                          unit, dclass, "measurement") | {"suggested_display_precision": prec},
        })
if not sensors:
    raise SystemExit("no hwmon sensors")

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        for s in sensors:
            client.publish(s["config"], json.dumps(s["disc"]), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

def cleanup(*_):
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    os._exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

while True:
    for s in sensors:
        v = int(os.pread(s["fd"], 32, 0))
        client.publish(s["state"], str(v if s["scale"] == 1 else v / s["scale"]), retain=True)
    time.sleep(5.0)
