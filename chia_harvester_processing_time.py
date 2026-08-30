#!/usr/bin/env python3
"""Chia harvester processing time + plots → MQTT + Home Assistant discovery (debug.log)."""
import json, os, re, time
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_sensor_discovery, create_client, chia_root
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"chia-harvester-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ_TIME = f"chia_harvester_processing_time_{HOSTNAME}"
OBJ_PLOTS = f"chia_harvester_plots_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/chia_harvester_{HOSTNAME}/availability"
STATE_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/state"
STATE_PLOTS = f"{PREFIX}/sensor/{OBJ_PLOTS}/state"
CONFIG_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/config"
CONFIG_PLOTS = f"{PREFIX}/sensor/{OBJ_PLOTS}/config"

DISCOVERY_TIME = make_sensor_discovery(
    "Chia Harvester Processing Time", STATE_TIME, AVAIL_T, OBJ_TIME, DEVICE,
    unit="s", device_class="duration", state_class="measurement"
)
DISCOVERY_TIME["suggested_display_precision"] = 1
DISCOVERY_PLOTS = {
    "name": "Chia Harvester Plots",
    "state_topic": STATE_PLOTS,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "state_class": "measurement",
    "unique_id": OBJ_PLOTS,
    "device": DEVICE,
}

LINE = re.compile(r"plots were eligible for farming .* Time: ([\d.]+) s\. Total (\d+) plots")
LOG = chia_root() / "log" / "debug.log"

def open_log(from_end):
    f = open(LOG)
    if from_end:
        f.seek(0, os.SEEK_END)
    return f, os.fstat(f.fileno()).st_ino

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_TIME, json.dumps(DISCOVERY_TIME), retain=True)
        client.publish(CONFIG_PLOTS, json.dumps(DISCOVERY_PLOTS), retain=True)
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
            m = LINE.search(line)
            if m:
                client.publish(STATE_TIME, format(float(m.group(1)), ".15g"), retain=True)
                client.publish(STATE_PLOTS, m.group(2), retain=True)
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
