#!/usr/bin/env python3
"""Chia recompute server processing time → MQTT + Home Assistant discovery (journalctl -f)."""
import json, re, subprocess
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"chia-recompute-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ_TIME = f"chia_recompute_time_{HOSTNAME}"
OBJ_FAIL = f"chia_recompute_fail_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/chia_recompute_{HOSTNAME}/availability"

STATE_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/state"
STATE_FAIL = f"{PREFIX}/sensor/{OBJ_FAIL}/state"
CONFIG_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/config"
CONFIG_FAIL = f"{PREFIX}/sensor/{OBJ_FAIL}/config"

DISCOVERY_TIME = make_sensor_discovery(
    "Chia Recompute Time", STATE_TIME, AVAIL_T, OBJ_TIME, DEVICE,
    unit="ms", device_class="duration", state_class="measurement"
)
DISCOVERY_FAIL = {
    "name": "Chia Recompute Fail",
    "state_topic": STATE_FAIL,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "state_class": "measurement",
    "unique_id": OBJ_FAIL,
    "device": DEVICE,
}

TOOK = re.compile(r"took ([\d.]+) ms \(used_gpu = (\d+), is_fail = (\d+)\)")

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_TIME, json.dumps(DISCOVERY_TIME), retain=True)
        client.publish(CONFIG_FAIL, json.dumps(DISCOVERY_FAIL), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

proc = subprocess.Popen(
    ["journalctl", "-u", "chia_recompute_server", "-f", "-n", "0", "-o", "cat", "--no-pager"],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    bufsize=1,
)

try:
    for line in proc.stdout:
        m = TOOK.search(line)
        if not m:
            continue
        client.publish(STATE_TIME, f"{float(m.group(1)):.2f}", retain=True)
        client.publish(STATE_FAIL, str(int(m.group(3))), retain=True)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    if proc.poll() is None:
        proc.terminate()
        proc.wait()
