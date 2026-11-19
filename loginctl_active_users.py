#!/usr/bin/env python3
"""loginctl active users → MQTT + Home Assistant discovery (session_source())."""
import json, time
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client, handle_cli, slot_id, slot_lists
)
from lib.system_common import session_source

MACHINE_ID = get_machine_id()
DEVICE, DEVICE_ID = make_device()
CLIENT_ID = f"loginctl-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

handle_cli(CLIENT_ID, settings, **slot_lists(("sensor/loginctl_active_users",), availability=("loginctl",)))

OBJ = slot_id("loginctl_active_users")
AVAIL_T = f"{PREFIX}/sensor/{slot_id('loginctl')}/availability"
STATE = f"{PREFIX}/sensor/{OBJ}/state"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"

DISCOVERY = make_sensor_discovery("Loginctl Active Users", STATE, AVAIL_T, OBJ, DEVICE)

SESSIONS = session_source()

def active_users():
    return ",".join(sorted(SESSIONS.active_users()))

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(CONFIG, json.dumps(DISCOVERY))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        client.publish(STATE, active_users())
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
