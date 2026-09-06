#!/usr/bin/env python3
"""Monero network difficulty, hashrate, block reward → MQTT + HA (xmrchain.net)."""
import json, sys, time, http.client
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_common import get_hostname, get_mqtt_settings, create_client

HOSTNAME = get_hostname()
DEVICE_ID = "xmr_network"
DEVICE = {"identifiers": [DEVICE_ID], "name": "Monero"}
CLIENT_ID = f"xmr-network-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
ATOMIC = 1_000_000_000_000

OBJ_DIFF = "xmr_difficulty"
OBJ_HR = "xmr_network_hashrate"
OBJ_REW = "xmr_block_reward"
OBJ_RATE = "xmr_per_hs_day"
AVAIL_T = f"{PREFIX}/sensor/xmr_network/availability"
STATE_DIFF = f"{PREFIX}/sensor/{OBJ_DIFF}/state"
STATE_HR = f"{PREFIX}/sensor/{OBJ_HR}/state"
STATE_REW = f"{PREFIX}/sensor/{OBJ_REW}/state"
STATE_RATE = f"{PREFIX}/sensor/{OBJ_RATE}/state"
CONFIG_DIFF = f"{PREFIX}/sensor/{OBJ_DIFF}/config"
CONFIG_HR = f"{PREFIX}/sensor/{OBJ_HR}/config"
CONFIG_REW = f"{PREFIX}/sensor/{OBJ_REW}/config"
CONFIG_RATE = f"{PREFIX}/sensor/{OBJ_RATE}/config"

def sensor(name, state, obj, unit=None):
    d = {
        "name": name,
        "state_topic": state,
        "availability_topic": AVAIL_T,
        "payload_available": "online",
        "payload_not_available": "offline",
        "state_class": "measurement",
        "unique_id": obj,
        "device": DEVICE,
    }
    if unit is not None:
        d["unit_of_measurement"] = unit
    return d

DISCOVERY_DIFF = sensor("Monero Difficulty", STATE_DIFF, OBJ_DIFF)
DISCOVERY_HR = sensor("Monero Network Hashrate", STATE_HR, OBJ_HR, "H/s")
DISCOVERY_HR["suggested_display_precision"] = 0
DISCOVERY_REW = sensor("Monero Block Reward", STATE_REW, OBJ_REW, "XMR")
DISCOVERY_REW["suggested_display_precision"] = 6
DISCOVERY_RATE = sensor("Monero XMR per H/s per Day", STATE_RATE, OBJ_RATE, "XMR/H/s/d")

conn = http.client.HTTPSConnection("xmrchain.net", timeout=15)
conn.connect()

def get(path):
    conn.request("GET", path)
    resp = conn.getresponse()
    return json.loads(resp.read())

def metrics():
    ni = get("/api/networkinfo")["data"]
    difficulty = int(ni["difficulty"])
    net_hr = float(ni["hash_rate"])
    height = int(ni["height"])
    block = get(f"/api/block/{height - 1}")["data"]
    reward_atomic = next(tx["xmr_outputs"] for tx in block["txs"] if tx["coinbase"])
    reward = reward_atomic / ATOMIC
    rate = reward * 86400.0 / difficulty
    return difficulty, net_hr, reward, rate

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_DIFF, json.dumps(DISCOVERY_DIFF), retain=True)
        client.publish(CONFIG_HR, json.dumps(DISCOVERY_HR), retain=True)
        client.publish(CONFIG_REW, json.dumps(DISCOVERY_REW), retain=True)
        client.publish(CONFIG_RATE, json.dumps(DISCOVERY_RATE), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        difficulty, net_hr, reward, rate = metrics()
        client.publish(STATE_DIFF, str(difficulty), retain=True)
        client.publish(STATE_HR, format(net_hr, ".15g"), retain=True)
        client.publish(STATE_REW, format(reward, ".15g"), retain=True)
        client.publish(STATE_RATE, format(rate, ".15g"), retain=True)
        time.sleep(60.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    conn.close()
