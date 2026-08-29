#!/usr/bin/env python3
"""LightDM unit active → MQTT + Home Assistant binary sensor (cgroup)."""
import json, os, time
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"lightdm-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ = f"lightdm_active_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/binary_sensor/lightdm_{HOSTNAME}/availability"
STATE = f"{PREFIX}/binary_sensor/{OBJ}/state"
CONFIG = f"{PREFIX}/binary_sensor/{OBJ}/config"

DISCOVERY = {
    "name": "LightDM Active",
    "state_topic": STATE,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "payload_on": "ON",
    "payload_off": "OFF",
    "device_class": "running",
    "unique_id": OBJ,
    "device": DEVICE,
}

CGROUP = "/sys/fs/cgroup/system.slice/lightdm.service"

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
        client.publish(STATE, "ON" if os.path.isdir(CGROUP) else "OFF", retain=True)
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
