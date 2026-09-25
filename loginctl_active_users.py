#!/usr/bin/env python3
"""loginctl active users → MQTT + Home Assistant discovery (session_source())."""
import json, time
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, create_client, session_source
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

SESSIONS = session_source()

def active_users():
    return ",".join(sorted(SESSIONS.active_users()))

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
