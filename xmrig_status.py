#!/usr/bin/env python3
"""XMRig hashrate, reject ratio, mining switch, profitability factor → MQTT + HA."""
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
SPOT_TOPIC = os.getenv("ELECTRICITY_EUR_PER_KWH_TOPIC") or None
SPOT_FIXED = os.getenv("ELECTRICITY_EUR_PER_KWH")
SPOT_FIXED = float(SPOT_FIXED) if SPOT_FIXED not in (None, "") else None

OBJ_HR = f"xmrig_hashrate_{HOSTNAME}"
OBJ_REJ = f"xmrig_reject_ratio_{HOSTNAME}"
OBJ_SW = f"xmrig_mining_{HOSTNAME}"
OBJ_PF = f"xmrig_profitability_factor_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/xmrig_{HOSTNAME}/availability"
STATE_HR = f"{PREFIX}/sensor/{OBJ_HR}/state"
STATE_REJ = f"{PREFIX}/sensor/{OBJ_REJ}/state"
STATE_SW = f"{PREFIX}/switch/{OBJ_SW}/state"
STATE_PF = f"{PREFIX}/sensor/{OBJ_PF}/state"
CMD_SW = f"{PREFIX}/switch/{OBJ_SW}/set"
CONFIG_HR = f"{PREFIX}/sensor/{OBJ_HR}/config"
CONFIG_REJ = f"{PREFIX}/sensor/{OBJ_REJ}/config"
CONFIG_SW = f"{PREFIX}/switch/{OBJ_SW}/config"
CONFIG_PF = f"{PREFIX}/sensor/{OBJ_PF}/config"
TOPIC_POWER = f"{PREFIX}/sensor/cpu_package_power_{HOSTNAME}/state"
TOPIC_RATE = f"{PREFIX}/sensor/xmr_per_hs_day_{HOSTNAME}/state"
TOPIC_EUR = f"{PREFIX}/sensor/xmr_eur_price_{HOSTNAME}/state"

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
DISCOVERY_HR["suggested_display_precision"] = 0
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
DISCOVERY_PF = {
    "name": "XMRig Profitability Factor",
    "state_topic": STATE_PF,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "state_class": "measurement",
    "unique_id": OBJ_PF,
    "device": DEVICE,
}
DISCOVERY_PF["suggested_display_precision"] = 3

last_hr = 0.0
last_power = 0.0
power_w = None
xmr_rate = None
xmr_eur = None
spot = SPOT_FIXED

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

def profitability(eff_hr, eff_power):
    if None in (xmr_rate, xmr_eur, spot) or eff_hr <= 0 or eff_power <= 0 or spot <= 0:
        return None
    revenue = eff_hr * xmr_rate / 24.0 * xmr_eur
    cost = eff_power / 1000.0 * spot
    return revenue / cost

def publish_stats(data):
    global last_hr, last_power
    total = data["hashrate"]["total"]
    hr = float(total[1] if len(total) > 1 and total[1] is not None else (total[0] or 0))
    good = int(data["results"]["shares_good"])
    shares = int(data["results"]["shares_total"])
    rej = 0.0 if shares == 0 else (shares - good) * 100.0 / shares
    paused = bool(data["paused"])
    if not paused and hr > 0:
        last_hr = hr
    if not paused and power_w is not None and power_w > 0:
        last_power = power_w
    eff_hr = hr if not paused and hr > 0 else last_hr
    eff_power = power_w if not paused and power_w is not None and power_w > 0 else last_power
    client.publish(STATE_HR, format(hr, ".15g"), retain=True)
    client.publish(STATE_REJ, format(rej, ".15g"), retain=True)
    client.publish(STATE_SW, "OFF" if paused else "ON", retain=True)
    factor = profitability(eff_hr, eff_power)
    if factor is not None:
        client.publish(STATE_PF, format(factor, ".15g"), retain=True)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_HR, json.dumps(DISCOVERY_HR), retain=True)
        client.publish(CONFIG_REJ, json.dumps(DISCOVERY_REJ), retain=True)
        client.publish(CONFIG_SW, json.dumps(DISCOVERY_SW), retain=True)
        client.publish(CONFIG_PF, json.dumps(DISCOVERY_PF), retain=True)
        client.subscribe(CMD_SW)
        client.subscribe(TOPIC_POWER)
        client.subscribe(TOPIC_RATE)
        client.subscribe(TOPIC_EUR)
        if SPOT_TOPIC:
            client.subscribe(SPOT_TOPIC)
        client.publish(AVAIL_T, "online", retain=True)

def on_message(client, userdata, msg):
    global power_w, xmr_rate, xmr_eur, spot
    topic = msg.topic
    payload = msg.payload.decode()
    if topic == CMD_SW:
        if payload == "ON":
            rpc("resume")
        elif payload == "OFF":
            rpc("pause")
        publish_stats(summary())
        return
    if topic == TOPIC_POWER:
        power_w = float(payload)
    elif topic == TOPIC_RATE:
        xmr_rate = float(payload)
    elif topic == TOPIC_EUR:
        xmr_eur = float(payload)
    elif SPOT_TOPIC and topic == SPOT_TOPIC:
        spot = float(payload)

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
