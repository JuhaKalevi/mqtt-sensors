#!/usr/bin/env python3
"""Pearl network difficulty, block times, block reward → MQTT + HA (pearlchain.live)."""
import json, sys, time, http.client
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.mqtt_common import get_hostname, get_mqtt_settings, create_client, make_sensor_discovery

HOSTNAME = get_hostname()
DEVICE_ID = "pearl_network"
DEVICE = {"identifiers": [DEVICE_ID], "name": "Pearl", "manufacturer": "pearlchain.live", "model": "Network"}
CLIENT_ID = f"pearl-network-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
WORK_PER_DIFFICULTY = 2**64 / 65535

OBJ_DIFF = "pearl_difficulty"
OBJ_NOM = "pearl_nominal_block_time"
OBJ_PER = "pearl_difficulty_period_block_time"
OBJ_CUR = "pearl_current_block_time"
OBJ_REW = "pearl_block_reward"
OBJ_RATE = "pearl_per_hs_day"
AVAIL_T = f"{PREFIX}/sensor/pearl_network/availability"
STATE_DIFF = f"{PREFIX}/sensor/{OBJ_DIFF}/state"
STATE_NOM = f"{PREFIX}/sensor/{OBJ_NOM}/state"
STATE_PER = f"{PREFIX}/sensor/{OBJ_PER}/state"
STATE_CUR = f"{PREFIX}/sensor/{OBJ_CUR}/state"
STATE_REW = f"{PREFIX}/sensor/{OBJ_REW}/state"
STATE_RATE = f"{PREFIX}/sensor/{OBJ_RATE}/state"
ATTRS_CUR = f"{PREFIX}/sensor/{OBJ_CUR}/attributes"
CONFIG_DIFF = f"{PREFIX}/sensor/{OBJ_DIFF}/config"
CONFIG_NOM = f"{PREFIX}/sensor/{OBJ_NOM}/config"
CONFIG_PER = f"{PREFIX}/sensor/{OBJ_PER}/config"
CONFIG_CUR = f"{PREFIX}/sensor/{OBJ_CUR}/config"
CONFIG_REW = f"{PREFIX}/sensor/{OBJ_REW}/config"
CONFIG_RATE = f"{PREFIX}/sensor/{OBJ_RATE}/config"

def sensor(name, state, obj, unit=None, **extra):
    return make_sensor_discovery(name, state, AVAIL_T, obj, DEVICE, unit=unit, state_class="measurement", **extra)

DISCOVERY_DIFF = sensor("Pearl Difficulty", STATE_DIFF, OBJ_DIFF)
DISCOVERY_NOM = sensor("Pearl Nominal Block Time", STATE_NOM, OBJ_NOM, "s", suggested_display_precision=0)
DISCOVERY_PER = sensor("Pearl Difficulty Period Block Time", STATE_PER, OBJ_PER, "s", suggested_display_precision=0)
DISCOVERY_CUR = sensor("Pearl Current Block Time", STATE_CUR, OBJ_CUR, "s", suggested_display_precision=1, json_attributes_topic=ATTRS_CUR)
DISCOVERY_REW = sensor("Pearl Block Reward", STATE_REW, OBJ_REW, "PEARL", suggested_display_precision=6)
DISCOVERY_RATE = sensor("Pearl PEARL per H/s per Day", STATE_RATE, OBJ_RATE, "PEARL/H/s/d")

conn = http.client.HTTPSConnection("pearlchain.live", timeout=15)

def get(path):
    conn.request("GET", path)
    resp = conn.getresponse()
    return json.loads(resp.read())

def metrics():
    st = get("/api/explorer/stats")
    difficulty = float(st["difficulty"])
    nominal = float(st["targetBlockSecs"])
    period = nominal
    window_secs = int(st["hashWindowSecs"])
    window_blocks = int(st["hashWindowBlocks"])
    current = window_secs / window_blocks
    reward = float(st["blockRewardPearl"])
    rate = reward * 86400.0 / (difficulty * WORK_PER_DIFFICULTY)
    return difficulty, reward, rate, nominal, period, current, window_secs, window_blocks

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(CONFIG_DIFF, json.dumps(DISCOVERY_DIFF))
        client.publish(CONFIG_NOM, json.dumps(DISCOVERY_NOM))
        client.publish(CONFIG_PER, json.dumps(DISCOVERY_PER))
        client.publish(CONFIG_CUR, json.dumps(DISCOVERY_CUR))
        client.publish(CONFIG_REW, json.dumps(DISCOVERY_REW))
        client.publish(CONFIG_RATE, json.dumps(DISCOVERY_RATE))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T, replay_states=True)
conn.connect()
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        difficulty, reward, rate, nominal, period, current, window_secs, window_blocks = metrics()
        client.publish(STATE_DIFF, format(difficulty, ".15g"))
        client.publish(STATE_NOM, format(nominal, ".15g"))
        client.publish(STATE_PER, format(period, ".15g"))
        client.publish(STATE_CUR, format(current, ".15g"))
        client.publish(ATTRS_CUR, json.dumps({"window_secs": window_secs, "window_blocks": window_blocks}))
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
