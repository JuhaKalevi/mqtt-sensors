#!/usr/bin/env python3
"""Chia recompute server processing time → MQTT + Home Assistant discovery (service_manager().follow_log)."""
import json, re
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client, slot_id
)
from lib.system_common import service_manager

MACHINE_ID = get_machine_id()
DEVICE, DEVICE_ID = make_device()
CLIENT_ID = f"chia-recompute-server-{MACHINE_ID}"
SERVICE = "chia_recompute_server"
SERVICES = service_manager()
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ_TIME = slot_id("chia_recompute_server_processing_time")
AVAIL_T = f"{PREFIX}/sensor/{slot_id('chia_recompute_server')}/availability"

STATE_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/state"
CONFIG_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/config"

DISCOVERY_TIME = make_sensor_discovery(
    "Chia Recompute Server Processing Time", STATE_TIME, AVAIL_T, OBJ_TIME, DEVICE,
    unit="s", device_class="duration", state_class="measurement", suggested_display_precision=1
)

TOOK = re.compile(r"took ([\d.]+) ms")

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(CONFIG_TIME, json.dumps(DISCOVERY_TIME))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

with SERVICES.follow_log(SERVICE) as lines:
    try:
        for line in lines:
            m = TOOK.search(line)
            if not m:
                continue
            client.publish(STATE_TIME, format(float(m.group(1)) / 1000.0, ".15g"))
    except KeyboardInterrupt:
        pass
    finally:
        client.publish(AVAIL_T, "offline")
        client.loop_stop()
        client.disconnect()
