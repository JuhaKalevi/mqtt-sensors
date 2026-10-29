#!/usr/bin/env python3
"""Previous system reset reason (x86/amd, current boot) → MQTT + Home Assistant discovery, once (/dev/kmsg)."""
import json, os, re
from mqtt_common import get_hostname, get_mqtt_settings, make_device, create_client

HOSTNAME = get_hostname()
DEVICE, _ = make_device(HOSTNAME)
CLIENT_ID = f"kmsg-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ = f"kmsg_reset_reason_{HOSTNAME}"
STATE = f"{PREFIX}/sensor/{OBJ}/state"
ATTRS = f"{PREFIX}/sensor/{OBJ}/attributes"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"
DISCOVERY = {
    "name": "Previous System Reset Reason",
    "state_topic": STATE,
    "json_attributes_topic": ATTRS,
    "unique_id": OBJ,
    "device": DEVICE,
}

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

code, reason = re.search(r"Previous system reset reason \[(0x[0-9a-fA-F]+)\]: (.*)", kmsg()).groups()
print(f"kmsg_reset_reason: host={HOSTNAME} code={code} reason={reason}", flush=True)

client = create_client(CLIENT_ID, settings)
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()
for info in [
    client.publish(CONFIG, json.dumps(DISCOVERY), qos=1, retain=True),
    client.publish(ATTRS, json.dumps({"code": code}), qos=1, retain=True),
    client.publish(STATE, reason, qos=1, retain=True),
]:
    info.wait_for_publish()
client.loop_stop()
client.disconnect()
