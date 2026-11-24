#!/usr/bin/env python3
"""Previous system reset reason (x86/amd, current boot) → MQTT + Home Assistant discovery, once (/dev/kmsg)."""
import json, os, re
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client, slot_id
)

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device()
CLIENT_ID = f"kmsg-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ = slot_id("kmsg_reset_reason")
STATE = f"{PREFIX}/sensor/{OBJ}/state"
ATTRS = f"{PREFIX}/sensor/{OBJ}/attributes"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"
DISCOVERY = make_sensor_discovery("Previous System Reset Reason", STATE, None, OBJ, DEVICE, json_attributes_topic=ATTRS)  # run-once: no availability

def kmsg():
    fd = os.open("/dev/kmsg", os.O_RDONLY | os.O_NONBLOCK)
    records = []
    try:
        while True:
            records.append(os.read(fd, 8192).decode(errors="replace"))
    except BlockingIOError:
        return "".join(records)
    finally:
        os.close(fd)

match = re.search(r"Previous system reset reason \[(0x[0-9a-fA-F]+)\]: (.*)", kmsg())
code, reason = match.groups() if match else (None, "clean shutdown")
print(f"kmsg_reset_reason: machine={MACHINE_ID} code={code} reason={reason}", flush=True)

client = create_client(CLIENT_ID, settings)
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()
for info in [
    client.publish(CONFIG, json.dumps(DISCOVERY), qos=1, retain=True),  # retained: run-once
    client.publish(ATTRS, json.dumps({"code": code}), qos=1, retain=True),  # retained: run-once
    client.publish(STATE, reason, qos=1, retain=True),  # retained: run-once
]:
    info.wait_for_publish()
client.loop_stop()
client.disconnect()
