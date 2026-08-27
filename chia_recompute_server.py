#!/usr/bin/env python3
"""Chia recompute server processing time → MQTT + Home Assistant discovery (journalctl -f)."""
import json, re, subprocess
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"chia-recompute-server-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ_TIME = f"chia_recompute_server_processing_time_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/chia_recompute_server_{HOSTNAME}/availability"

STATE_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/state"
CONFIG_TIME = f"{PREFIX}/sensor/{OBJ_TIME}/config"

DISCOVERY_TIME = make_sensor_discovery(
    "Chia Recompute Server Processing Time", STATE_TIME, AVAIL_T, OBJ_TIME, DEVICE,
    unit="s", device_class="duration", state_class="measurement"
)

TOOK = re.compile(r"took ([\d.]+) ms")

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_TIME, json.dumps(DISCOVERY_TIME), retain=True)
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
        client.publish(STATE_TIME, f"{float(m.group(1)) / 1000.0:.1f}", retain=True)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    if proc.poll() is None:
        proc.terminate()
        proc.wait()
