#!/usr/bin/env python3
"""Chia farm size → MQTT + Home Assistant discovery (farmer get_harvesters_summary)."""
import time, json, ssl, urllib.request
from pathlib import Path
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"chia-farm-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ_PLOTS = f"chia_plots_{HOSTNAME}"
OBJ_SIZE = f"chia_farm_size_{HOSTNAME}"
OBJ_EFF = f"chia_farm_effective_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/chia_farm_{HOSTNAME}/availability"

STATE_PLOTS = f"{PREFIX}/sensor/{OBJ_PLOTS}/state"
STATE_SIZE = f"{PREFIX}/sensor/{OBJ_SIZE}/state"
STATE_EFF = f"{PREFIX}/sensor/{OBJ_EFF}/state"
CONFIG_PLOTS = f"{PREFIX}/sensor/{OBJ_PLOTS}/config"
CONFIG_SIZE = f"{PREFIX}/sensor/{OBJ_SIZE}/config"
CONFIG_EFF = f"{PREFIX}/sensor/{OBJ_EFF}/config"

DISCOVERY_PLOTS = {
    "name": "Chia Plots",
    "state_topic": STATE_PLOTS,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "state_class": "measurement",
    "unique_id": OBJ_PLOTS,
    "device": DEVICE,
}
DISCOVERY_SIZE = make_sensor_discovery(
    "Chia Farm Size", STATE_SIZE, AVAIL_T, OBJ_SIZE, DEVICE,
    unit="TiB", device_class="data_size", state_class="measurement"
)
DISCOVERY_EFF = make_sensor_discovery(
    "Chia Farm Effective Size", STATE_EFF, AVAIL_T, OBJ_EFF, DEVICE,
    unit="TiB", device_class="data_size", state_class="measurement"
)

CHIA_ROOT = Path.home() / ".chia" / "mainnet"
CERT = CHIA_ROOT / "config" / "ssl" / "farmer" / "private_farmer.crt"
KEY = CHIA_ROOT / "config" / "ssl" / "farmer" / "private_farmer.key"
URL = "https://localhost:8559/get_harvesters_summary"
TIB = 1024 ** 4

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE
ctx.load_cert_chain(certfile=str(CERT), keyfile=str(KEY))

def farm_stats():
    req = urllib.request.Request(
        URL, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        data = json.load(resp)
    plots = size = effective = 0
    for h in data["harvesters"]:
        plots += int(h.get("plots") or 0)
        size += int(h.get("total_plot_size") or 0)
        effective += int(h.get("total_effective_plot_size") or 0)
    return plots, size / TIB, effective / TIB

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_PLOTS, json.dumps(DISCOVERY_PLOTS), retain=True)
        client.publish(CONFIG_SIZE, json.dumps(DISCOVERY_SIZE), retain=True)
        client.publish(CONFIG_EFF, json.dumps(DISCOVERY_EFF), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        plots, size, effective = farm_stats()
        client.publish(STATE_PLOTS, str(plots), retain=True)
        client.publish(STATE_SIZE, f"{size:.3f}", retain=True)
        client.publish(STATE_EFF, f"{effective:.3f}", retain=True)
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
