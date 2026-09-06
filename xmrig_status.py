#!/usr/bin/env python3
"""XMRig hashrate, reject ratio, mining switch → MQTT + Home Assistant discovery."""
import json, os, time, http.client
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"xmrig-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
XMRIG_HOST = os.getenv("XMRIG_HOST", "127.0.0.1")
XMRIG_PORT = int(os.getenv("XMRIG_PORT", "44444"))
XMRIG_TOKEN = os.getenv("XMRIG_TOKEN") or None

OBJ_HR = f"xmrig_hashrate_{HOSTNAME}"
OBJ_REJ = f"xmrig_reject_ratio_{HOSTNAME}"
OBJ_SW = f"xmrig_mining_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/xmrig_{HOSTNAME}/availability"
STATE_HR = f"{PREFIX}/sensor/{OBJ_HR}/state"
STATE_REJ = f"{PREFIX}/sensor/{OBJ_REJ}/state"
STATE_SW = f"{PREFIX}/switch/{OBJ_SW}/state"
CMD_SW = f"{PREFIX}/switch/{OBJ_SW}/set"
CONFIG_HR = f"{PREFIX}/sensor/{OBJ_HR}/config"
CONFIG_REJ = f"{PREFIX}/sensor/{OBJ_REJ}/config"
CONFIG_SW = f"{PREFIX}/switch/{OBJ_SW}/config"

DISCOVERY_HR = {
    "name": "XMRig Hashrate",
    "state_topic": STATE_HR,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "unit_of_measurement": "H/s",
    "state_class": "measurement",
    "unique_id": OBJ_HR,
    "device": DEVICE,
}
DISCOVERY_REJ = {
    "name": "XMRig Reject Ratio",
    "state_topic": STATE_REJ,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "unit_of_measurement": "%",
    "state_class": "measurement",
    "unique_id": OBJ_REJ,
    "device": DEVICE,
}
DISCOVERY_SW = {
    "name": "XMRig Mining",
    "state_topic": STATE_SW,
    "command_topic": CMD_SW,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "payload_on": "ON",
    "payload_off": "OFF",
    "unique_id": OBJ_SW,
    "device": DEVICE,
}

def auth_headers():
    h = {"Content-Type": "application/json"}
    if XMRIG_TOKEN:
        h["Authorization"] = f"Bearer {XMRIG_TOKEN}"
    return h

conn = http.client.HTTPConnection(XMRIG_HOST, XMRIG_PORT, timeout=5)
conn.connect()

def api(method, path, body=None):
    payload = None if body is None else json.dumps(body).encode()
    conn.request(method, path, body=payload, headers=auth_headers())
    resp = conn.getresponse()
    data = resp.read()
    return json.loads(data) if data else {}

def summary():
    return api("GET", "/2/summary")

def rpc(method):
    api("POST", "/json_rpc", {"method": method, "id": 1})

def publish_stats(data):
    total = data["hashrate"]["total"]
    hr = total[1] if len(total) > 1 and total[1] is not None else (total[0] or 0)
    good = int(data["results"]["shares_good"])
    shares = int(data["results"]["shares_total"])
    rej = 0.0 if shares == 0 else (shares - good) * 100.0 / shares
    client.publish(STATE_HR, format(float(hr), ".15g"), retain=True)
    client.publish(STATE_REJ, format(rej, ".15g"), retain=True)
    client.publish(STATE_SW, "OFF" if data["paused"] else "ON", retain=True)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_HR, json.dumps(DISCOVERY_HR), retain=True)
        client.publish(CONFIG_REJ, json.dumps(DISCOVERY_REJ), retain=True)
        client.publish(CONFIG_SW, json.dumps(DISCOVERY_SW), retain=True)
        client.subscribe(CMD_SW)
        client.publish(AVAIL_T, "online", retain=True)

def on_message(client, userdata, msg):
    payload = msg.payload.decode()
    if payload == "ON":
        rpc("resume")
    elif payload == "OFF":
        rpc("pause")
    publish_stats(summary())

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.on_message = on_message
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        publish_stats(summary())
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    conn.close()
