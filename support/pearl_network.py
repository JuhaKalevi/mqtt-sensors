#!/usr/bin/env python3
"""Pearl network difficulty, hashrate, block reward → MQTT + HA (pearlchain.live)."""
import json, sys, time, http.client
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_common import get_hostname, get_mqtt_settings, create_client

HOSTNAME = get_hostname()
DEVICE_ID = "pearl_network"
DEVICE = {"identifiers": [DEVICE_ID], "name": "Pearl"}
CLIENT_ID = f"pearl-network-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
WORK_PER_DIFFICULTY = 2**64 / 65535

OBJ_DIFF = "pearl_difficulty"
OBJ_HR = "pearl_network_hashrate"
OBJ_REW = "pearl_block_reward"
OBJ_RATE = "pearl_per_hs_day"
AVAIL_T = f"{PREFIX}/sensor/pearl_network/availability"
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

DISCOVERY_DIFF = sensor("Pearl Difficulty", STATE_DIFF, OBJ_DIFF)
DISCOVERY_HR = sensor("Pearl Network Hashrate", STATE_HR, OBJ_HR, "H/s")
DISCOVERY_HR["suggested_display_precision"] = 0
DISCOVERY_REW = sensor("Pearl Block Reward", STATE_REW, OBJ_REW, "PEARL")
DISCOVERY_REW["suggested_display_precision"] = 6
DISCOVERY_RATE = sensor("Pearl PEARL per H/s per Day", STATE_RATE, OBJ_RATE, "PEARL/H/s/d")

conn = http.client.HTTPSConnection("pearlchain.live", timeout=15)
conn.connect()

def get(path):
    conn.request("GET", path)
    resp = conn.getresponse()
    return json.loads(resp.read())

def metrics():
    st = get("/api/explorer/stats")
    difficulty = float(st["difficulty"])
    target_secs = float(st["targetBlockSecs"])
    net_hr = difficulty * WORK_PER_DIFFICULTY / target_secs
    reward = float(st["blockRewardPearl"])
    rate = reward * 86400.0 / (difficulty * WORK_PER_DIFFICULTY)
    return difficulty, net_hr, reward, rate

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(CONFIG_DIFF, json.dumps(DISCOVERY_DIFF))
        client.publish(CONFIG_HR, json.dumps(DISCOVERY_HR))
        client.publish(CONFIG_REW, json.dumps(DISCOVERY_REW))
        client.publish(CONFIG_RATE, json.dumps(DISCOVERY_RATE))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T, replay_states=True)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        difficulty, net_hr, reward, rate = metrics()
        client.publish(STATE_DIFF, format(difficulty, ".15g"))
        client.publish(STATE_HR, format(net_hr, ".15g"))
        client.publish(STATE_REW, format(reward, ".15g"))
        client.publish(STATE_RATE, format(rate, ".15g"))
        time.sleep(60.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    conn.close()
