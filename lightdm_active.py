#!/usr/bin/env python3
"""LightDM service active → MQTT + Home Assistant switch (service_manager(); start/stop only in control mode)."""
import json, time
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, create_client,
    make_switch_discovery, switch_mode, make_switch_on_message, slot_id
)
from lib.system_common import service_manager

SERVICE = "lightdm"
MACHINE_ID = get_machine_id()
DEVICE, DEVICE_ID = make_device()
CLIENT_ID = f"lightdm-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
SERVICES = service_manager()
MODE = switch_mode("LIGHTDM_SWITCH_MODE")

OBJ = slot_id("lightdm_active")
AVAIL_T = f"{PREFIX}/sensor/{slot_id('lightdm')}/availability"
STATE = f"{PREFIX}/switch/{OBJ}/state"
CMD = f"{PREFIX}/switch/{OBJ}/set"
CONFIG = f"{PREFIX}/switch/{OBJ}/config"
DISCOVERY = make_switch_discovery("LightDM Active", STATE, CMD, AVAIL_T, OBJ, DEVICE)

def state():
    return "ON" if SERVICES.is_active(SERVICE) else "OFF"

def act(payload):
    (SERVICES.start if payload == "ON" else SERVICES.stop)(SERVICE)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(CONFIG, json.dumps(DISCOVERY))
        client.subscribe(CMD)
        client.publish(AVAIL_T, "online")

print(f"lightdm_active: machine={MACHINE_ID} mode={MODE}", flush=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.on_message = make_switch_on_message("lightdm_active", MODE, STATE, state, act)
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        client.publish(STATE, state())
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
