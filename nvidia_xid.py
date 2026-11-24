#!/usr/bin/env python3
"""NVIDIA Xid errors → MQTT + Home Assistant discovery (/dev/kmsg)."""
import json, os, re, signal
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client, slot_id
)

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device()
CLIENT_ID = f"nvidia-xid-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
OBJ = slot_id("nvidia_xid")
AVAIL_T = f"{PREFIX}/sensor/{OBJ}/availability"
STATE = f"{PREFIX}/sensor/{OBJ}/state"
ATTRS = f"{PREFIX}/sensor/{OBJ}/attributes"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"
DISCOVERY = make_sensor_discovery("NVIDIA Xid Errors", STATE, AVAIL_T, OBJ, DEVICE, None, None, "total_increasing",
                                  json_attributes_topic=ATTRS, entity_category="diagnostic")
XID_RE = re.compile(r"NVRM:\s+Xid\s+\(([^)]+)\):\s+(\d+)")

def record_text(record):
    return record.partition(";")[2].strip()

def parse_xid(record):
    match = XID_RE.search(record_text(record))
    if not match:
        return None
    return match.group(1).strip(), int(match.group(2))

fd = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK)
count = 0
last_xid = None
last_source = None

def drain():
    global count, last_xid, last_source
    found = []
    try:
        while True:
            rec = os.read(fd, 8192).decode(errors="replace")
            if not rec:
                break
            parsed = parse_xid(rec)
            if parsed:
                last_source, last_xid = parsed
                count += 1
                found.append(parsed)
    except BlockingIOError:
        pass
    return found

drain()

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(CONFIG, json.dumps(DISCOVERY))
        client.publish(STATE, str(count))
        client.publish(ATTRS, json.dumps({"last_xid": last_xid, "last_source": last_source}))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

def cleanup(*_):
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    os.close(fd)
    os._exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

os.set_blocking(fd, True)
try:
    while True:
        rec = os.read(fd, 8192).decode(errors="replace")
        if not rec:
            break
        parsed = parse_xid(rec)
        if not parsed:
            continue
        last_source, last_xid = parsed
        count += 1
        client.publish(STATE, str(count))
        client.publish(ATTRS, json.dumps({"last_xid": last_xid, "last_source": last_source}))
except KeyboardInterrupt:
    pass
finally:
    cleanup()
