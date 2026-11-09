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
cleared = set()

def read_opt(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""

def slug(text):
    out = []
    gap = False
    for c in text:
        if c.isalnum():
            out.append(c)
            gap = False
        elif not gap:
            out.append("_")
            gap = True
    return "".join(out).strip("_")

def battery_key(d):
    model = read_opt(d / "model_name")
    if model:
        return slug(model)
    return d.name

def object_id(key):
    return f"power_supply_{key}_{HOSTNAME}"

def discovery_for(d, key, peripheral):
    model = read_opt(d / "model_name")
    manufacturer = read_opt(d / "manufacturer")
    label = model or manufacturer or d.name.replace("_", " ")
    obj = object_id(key)
    state = f"{PREFIX}/sensor/{obj}/state"
    config = f"{PREFIX}/sensor/{obj}/config"
    if peripheral:
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
    else:
        device = HOST_DEVICE
        name = label if "battery" in label.lower() else f"{label} Battery"
    return state, config, make_sensor_discovery(
        name, state, AVAIL_T, obj, device,
        unit="%", device_class="battery", state_class="measurement"
    )

def clear_legacy(d, key):
    legacy = f"power_supply_{d.name}_{HOSTNAME}"
    if legacy == object_id(key) or legacy in cleared:
        return
    client.publish(f"{PREFIX}/sensor/{legacy}/config", "", retain=True)
    cleared.add(legacy)

def drop_battery(key):
    b = batteries.pop(key, None)
    if not b:
        return
    try:
        b["fd"].close()
    except OSError:
        pass
    client.publish(b["config"], "", retain=True)

def choose(dirs):
    return sorted(dirs, key=lambda d: (read_opt(d / "scope") == "System", d.name))[0]

def sync_batteries():
    groups = {}
    try:
        entries = list(ROOT.iterdir())
    except OSError:
        entries = []
    for d in entries:
        if not d.is_dir() or read_opt(d / "type") != "Battery":
            continue
        if not (d / "capacity").exists():
            continue
        groups.setdefault(battery_key(d), []).append(d)
    for key, dirs in groups.items():
        peripheral = any(read_opt(d / "scope") != "System" for d in dirs)
        picked = choose(dirs)
        for d in dirs:
            clear_legacy(d, key)
        b = batteries.get(key)
        if b and b["name"] not in {d.name for d in dirs}:
            try:
                b["fd"].close()
            except OSError:
                pass
            try:
                b["fd"] = open(picked / "capacity")
                b["name"] = picked.name
            except OSError:
                b["name"] = ""
        if b and b["peripheral"] != peripheral:
            state, config, disc = discovery_for(picked, key, peripheral)
            b["peripheral"] = peripheral
            b["state"] = state
            b["config"] = config
            b["discovery"] = disc
            client.publish(config, json.dumps(disc))
        if b:
            continue
        try:
            fd = open(picked / "capacity")
        except OSError:
            continue
        state, config, disc = discovery_for(picked, key, peripheral)
        batteries[key] = {
            "name": picked.name,
            "fd": fd,
            "state": state,
            "config": config,
            "peripheral": peripheral,
            "discovery": disc,
        }
        client.publish(config, json.dumps(disc))
    for key in list(batteries):
        if key not in groups:
            drop_battery(key)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        for b in batteries.values():
            client.publish(b["config"], json.dumps(b["discovery"]))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        sync_batteries()
        for key in list(batteries):
            b = batteries.get(key)
            if not b:
                continue
            try:
                f = b["fd"]
                f.seek(0)
                val = f.read().strip()
                if not val:
                    raise OSError("empty")
                client.publish(b["state"], val)
            except OSError:
                try:
                    b["fd"].close()
                except OSError:
                    pass
                b["name"] = ""
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    for key in list(batteries):
        drop_battery(key)
