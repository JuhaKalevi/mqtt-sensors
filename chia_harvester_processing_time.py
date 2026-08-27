#!/usr/bin/env python3
"""Chia harvester processing time → MQTT + Home Assistant discovery (debug.log)."""
import json, os, re, time
from pathlib import Path
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"chia-harvester-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ_TIME = f"chia_harvester_processing_time_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/chia_harvester_{HOSTNAME}/availability"
STATE_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/state"
CONFIG_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/config"

DISCOVERY_TIME = make_sensor_discovery(
    "Chia Harvester Processing Time", STATE_TIME, AVAIL_T, OBJ_TIME, DEVICE,
    unit="s", device_class="duration", state_class="measurement"
)
DISCOVERY_TIME["suggested_display_precision"] = 1

TOOK = re.compile(r"plots were eligible for farming .* Time: ([\d.]+) s")
LOG = Path.home() / ".chia" / "mainnet" / "log" / "debug.log"

def open_log(from_end):
    f = open(LOG)
    if from_end:
        f.seek(0, os.SEEK_END)
    return f, os.fstat(f.fileno()).st_ino

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_TIME, json.dumps(DISCOVERY_TIME), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

f, ino = open_log(True)
try:
    while True:
        line = f.readline()
        if line:
            m = TOOK.search(line)
            if m:
                client.publish(STATE_TIME, format(float(m.group(1)), ".15g"), retain=True)
            continue
        try:
            cur = os.stat(LOG).st_ino
        except FileNotFoundError:
            time.sleep(1.0)
            continue
        if cur != ino:
            f.close()
            f, ino = open_log(False)
            continue
        f.seek(f.tell())
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    f.close()
