#!/usr/bin/env python3
"""Power supply battery capacity → MQTT + Home Assistant discovery (sysfs)."""
import json, time
from pathlib import Path
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
HOST_DEVICE, HOST_DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"power-supply-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/power_supply_{HOSTNAME}/availability"
ROOT = Path("/sys/class/power_supply")

def read_opt(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""

def find_batteries():
    found = []
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir():
            continue
        try:
            if read_opt(d / "type") != "Battery":
                continue
            f = open(d / "capacity")
        except OSError:
            continue
        model = read_opt(d / "model_name")
        manufacturer = read_opt(d / "manufacturer")
        scope = read_opt(d / "scope")
        label = model or manufacturer or d.name.replace("_", " ")
        obj = f"power_supply_{d.name}_{HOSTNAME}"
        state = f"{PREFIX}/sensor/{obj}/state"
        config = f"{PREFIX}/sensor/{obj}/config"
        if scope == "System":
            device = HOST_DEVICE
            name = label if "battery" in label.lower() else f"{label} Battery"
        else:
            device = {
                "identifiers": [obj],
                "name": label,
                "via_device": HOST_DEVICE_ID,
            }
            if manufacturer:
                device["manufacturer"] = manufacturer
            if model:
                device["model"] = model
            name = "Battery"
        found.append({
            "fd": f,
            "state": state,
            "config": config,
            "discovery": make_sensor_discovery(
                name, state, AVAIL_T, obj, device,
                unit="%", device_class="battery", state_class="measurement"
            ),
        })
    return found

batteries = find_batteries()
if not batteries:
    raise SystemExit("no power_supply Battery with capacity")

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        for b in batteries:
            client.publish(b["config"], json.dumps(b["discovery"]), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        for b in batteries:
            f = b["fd"]
            f.seek(0)
            client.publish(b["state"], f.read().strip(), retain=True)
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    for b in batteries:
        b["fd"].close()
