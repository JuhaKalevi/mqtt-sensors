#!/usr/bin/env python3
"""Drive temperature (drivetemp + nvme hwmon) → MQTT + Home Assistant discovery (sysfs)."""
import json, os, re, sys, time
from pathlib import Path
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"hwmon-drive-temperature-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
INTERVAL = float(os.getenv("DRIVE_TEMPERATURE_INTERVAL") or "60")
AVAIL_T = f"{PREFIX}/sensor/hwmon_drive_temperature_{HOSTNAME}/availability"
ROOT = Path("/sys/class/hwmon")
drives = {}
skipped = {}

def read_opt(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""

def read_bin(path):
    try:
        return path.read_bytes()
    except OSError:
        return b""

def text(b):
    return "".join(c for c in b.decode("ascii", "ignore") if c.isprintable()).strip()

def ata_string(b):
    return text(bytes(b[i ^ 1] for i in range(len(b) & ~1)))

def usb_parent(dev):
    for p in dev.parents:
        if (p / "idVendor").is_file():
            return p
    return None

def identity(dev):
    usb = usb_parent(dev)
    pg89 = read_bin(dev / "vpd_pg89")
    model = ata_string(pg89[114:154]) if len(pg89) >= 572 else ""
    serial = ata_string(pg89[80:100]) if len(pg89) >= 572 else ""
    model = model or read_opt(dev / "model")
    serial = serial or read_opt(dev / "serial") or text(read_bin(dev / "vpd_pg80")[4:])
    ident = serial or read_opt(dev / "wwid")
    if not ident and usb:
        usb_serial = read_opt(usb / "serial")
        if usb_serial:
            lun = dev.name.split(":")[-1]
            ident = f"usb {read_opt(usb / 'idVendor')} {read_opt(usb / 'idProduct')} {usb_serial} {lun}"
    if not ident:
        return None
    return model or "Drive", serial, re.sub(r"[^a-z0-9]+", "_", ident.lower()).strip("_"), usb is not None

def open_drive(hw, dev):
    if read_opt(hw / "name") not in ("drivetemp", "nvme"):
        return None
    ids = identity(dev)
    if not ids:
        if skipped.get(hw.name) != dev:
            skipped[hw.name] = dev
            print(f"hwmon_drive_temperature: skipping {hw.name} ({dev}): no stable serial, wwid or USB serial", flush=True)
        return None
    try:
        f = open(hw / "temp1_input")
    except OSError:
        return None
    model, serial, ident, usb = ids
    obj = f"hwmon_drive_temperature_{ident}_{HOSTNAME}"
    state = f"{PREFIX}/sensor/{obj}/state"
    name = f"{model} {serial}" if serial else model
    name = f"{name} Temperature (USB)" if usb else f"{name} Temperature"
    return {
        "dev": dev,
        "fd": f,
        "state": state,
        "config": f"{PREFIX}/sensor/{obj}/config",
        "discovery": make_sensor_discovery(
            name, state, AVAIL_T, obj, DEVICE,
            unit="°C", device_class="temperature", state_class="measurement"
        ),
    }

def drop_drive(name):
    d = drives.pop(name, None)
    if not d:
        return
    try:
        d["fd"].close()
    except OSError:
        pass
    client.publish(d["config"], "", retain=True)

def sync_drives():
    present = set()
    for hw in sorted(ROOT.iterdir()):
        present.add(hw.name)
        dev = (hw / "device").resolve()
        if hw.name in drives:
            if drives[hw.name]["dev"] == dev:
                continue
            drop_drive(hw.name)
        d = open_drive(hw, dev)
        if not d:
            continue
        drives[hw.name] = d
        client.publish(d["config"], json.dumps(d["discovery"]), retain=True)
    for name in list(drives):
        if name not in present:
            drop_drive(name)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        for d in drives.values():
            client.publish(d["config"], json.dumps(d["discovery"]), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

if not any(read_opt(hw / "name") in ("drivetemp", "nvme") for hw in ROOT.iterdir()):
    sys.exit(f"hwmon_drive_temperature: no drivetemp or nvme hwmon under {ROOT}; load the module: modprobe drivetemp")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        sync_drives()
        for d in drives.values():
            try:
                d["fd"].seek(0)
                val = int(d["fd"].read())
            except (OSError, ValueError):
                continue
            client.publish(d["state"], "%.1f" % (val / 1000.0), retain=True)
        time.sleep(INTERVAL)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    for d in drives.values():
        d["fd"].close()
