#!/usr/bin/env python3
"""loginctl active users → MQTT + Home Assistant discovery (/run/systemd/users)."""
import json, os, time
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"loginctl-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ = f"loginctl_active_users_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/loginctl_{HOSTNAME}/availability"
STATE = f"{PREFIX}/sensor/{OBJ}/state"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"

DISCOVERY = {
    "name": "Loginctl Active Users",
    "state_topic": STATE,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "unique_id": OBJ,
    "device": DEVICE,
}

USERS = "/run/systemd/users"

def active_users():
    names = []
    for fn in os.listdir(USERS):
        if not fn.isdigit():
            continue
        user = state = None
        try:
            with open(os.path.join(USERS, fn)) as f:
                for line in f:
                    if line.startswith("NAME="):
                        user = line[5:].strip()
                    elif line.startswith("STATE="):
                        state = line[6:].strip()
        except FileNotFoundError:
            continue
        if user and state in ("active", "online"):
            names.append(user)
    names.sort()
    return ",".join(names)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG, json.dumps(DISCOVERY), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        client.publish(STATE, active_users(), retain=True)
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
