#!/usr/bin/env python3
"""Power supply battery capacity → MQTT + Home Assistant discovery (sysfs)."""
import json, time
from pathlib import Path
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_child_device, make_sensor_discovery, create_client, machine_count,
    slot_id
)

MACHINE_ID = get_machine_id()
HOST_DEVICE, _ = make_device()
CLIENT_ID = f"power-supply-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/{slot_id('power_supply')}/availability"
COUNT = machine_count(PREFIX, AVAIL_T, "Battery Count", "power_supply_battery_count", HOST_DEVICE)
ROOT = Path("/sys/class/power_supply")
batteries = {}

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

def object_id(key, dirs, peripheral):
    """System battery: slot-keyed. Peripheral: keyed by the device (model + first serial_number of the group), no slot, so it keeps its entity on any machine."""
    if not peripheral:
        return slot_id(f"power_supply_{key}")
    serial = next((s for s in (read_opt(d / "serial_number") for d in dirs) if s), "")
    return f"power_supply_{key}_{slug(serial)}" if serial else f"power_supply_{key}"

def scan():
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
    return groups

def peripheral_of(dirs):
    return any(read_opt(d / "scope") != "System" for d in dirs)

def discovery_for(d, key, peripheral, dirs):
    model = read_opt(d / "model_name")
    manufacturer = read_opt(d / "manufacturer")
    label = model or manufacturer or d.name.replace("_", " ")
    obj = object_id(key, dirs, peripheral)
    state = f"{PREFIX}/sensor/{obj}/state"
    config = f"{PREFIX}/sensor/{obj}/config"
    if peripheral:
        device = make_child_device(HOST_DEVICE, obj, label)
        name = "Battery"
    else:
        device = HOST_DEVICE
        name = label if "battery" in label.lower() else f"{label} Battery"
    return state, config, make_sensor_discovery(
        name, state, AVAIL_T, obj, device,
        unit="%", device_class="battery", state_class="measurement"
    )

def drop_battery(key):
    b = batteries.pop(key, None)
    if not b:
        return
    try:
        b["fd"].close()
    except OSError:
        pass
    # retained: clear stored message. A machine that has this peripheral now gets the cleared config too, and
    # republishes it (rediscovery) as its own child: HA cannot move a child device to another parent otherwise.
    client.publish(b["config"], "", retain=True)

def choose(dirs):
    return sorted(dirs, key=lambda d: (read_opt(d / "scope") == "System", d.name))[0]

def sync_batteries():
    groups = scan()
    for key, dirs in groups.items():
        peripheral = peripheral_of(dirs)
        picked = choose(dirs)
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
            client.publish(b["config"], "", retain=True)  # retained: clear stored message
            state, config, disc = discovery_for(picked, key, peripheral, dirs)
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
        state, config, disc = discovery_for(picked, key, peripheral, dirs)
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
        client.publish(COUNT["config"], json.dumps(COUNT["discovery"]))
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
        client.publish(COUNT["state"], str(len(batteries)))
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
