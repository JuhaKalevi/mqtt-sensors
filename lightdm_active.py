#!/usr/bin/env python3
"""LightDM service active → MQTT + Home Assistant switch (service_manager(); start/stop only in control mode)."""
import json, time
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, create_client, make_switch_discovery,
    switch_mode, make_switch_on_message, service_manager
)

SERVICE = "lightdm"
HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"lightdm-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
SERVICES = service_manager()
MODE = switch_mode("LIGHTDM_SWITCH_MODE")

OBJ = f"lightdm_active_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/binary_sensor/lightdm_{HOSTNAME}/availability"
STATE = f"{PREFIX}/switch/{OBJ}/state"
CMD = f"{PREFIX}/switch/{OBJ}/set"
CONFIG = f"{PREFIX}/switch/{OBJ}/config"
OLD_STATE = f"{PREFIX}/binary_sensor/{OBJ}/state"
OLD_CONFIG = f"{PREFIX}/binary_sensor/{OBJ}/config"
DISCOVERY = make_switch_discovery("LightDM Active", STATE, CMD, AVAIL_T, OBJ, DEVICE)

def state():
    return "ON" if SERVICES.is_active(SERVICE) else "OFF"

def act(payload):
    (SERVICES.start if payload == "ON" else SERVICES.stop)(SERVICE)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(OLD_CONFIG, "", retain=True)
        client.publish(OLD_STATE, "", retain=True)
        client.publish(CONFIG, json.dumps(DISCOVERY), retain=True)
        client.subscribe(CMD)
        client.publish(AVAIL_T, "online", retain=True)

print(f"lightdm_active: host={HOSTNAME} mode={MODE}", flush=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.on_message = make_switch_on_message("lightdm_active", MODE, STATE, state, act)
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        client.publish(STATE, state(), retain=True)
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
