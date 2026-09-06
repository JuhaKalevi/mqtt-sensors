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
batteries = {}

def read_opt(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""

def open_battery(d):
    try:
        if read_opt(d / "type") != "Battery":
            return None
        f = open(d / "capacity")
    except OSError:
        return None
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
    return {
        "name": d.name,
        "fd": f,
        "state": state,
        "config": config,
        "discovery": make_sensor_discovery(
            name, state, AVAIL_T, obj, device,
            unit="%", device_class="battery", state_class="measurement"
        ),
    }

def drop_battery(name):
    b = batteries.pop(name, None)
    if not b:
        return
    try:
        b["fd"].close()
    except OSError:
        pass
    client.publish(b["config"], "", retain=True)

def sync_batteries():
    present = set()
    try:
        entries = sorted(ROOT.iterdir())
    except OSError:
        entries = []
    for d in entries:
        if not d.is_dir():
            continue
        present.add(d.name)
        if d.name in batteries:
            continue
        b = open_battery(d)
        if not b:
            continue
        batteries[d.name] = b
        client.publish(b["config"], json.dumps(b["discovery"]), retain=True)
    for name in list(batteries):
        if name not in present:
            drop_battery(name)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        for b in batteries.values():
            client.publish(b["config"], json.dumps(b["discovery"]), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        sync_batteries()
        for name in list(batteries):
            b = batteries.get(name)
            if not b:
                continue
            try:
                f = b["fd"]
                f.seek(0)
                val = f.read().strip()
                if not val:
                    raise OSError("empty")
                client.publish(b["state"], val, retain=True)
            except OSError:
                drop_battery(name)
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    for name in list(batteries):
        drop_battery(name)
