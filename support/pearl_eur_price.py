#!/usr/bin/env python3
"""PEARL/EUR spot price → MQTT + Home Assistant discovery (CoinGecko public simple price)."""
import json, sys, time, http.client
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_common import get_hostname, get_mqtt_settings, create_client

HOSTNAME = get_hostname()
DEVICE_ID = "pearl_eur_price"
DEVICE = {"identifiers": [DEVICE_ID], "name": "PEARL/EUR"}
CLIENT_ID = f"pearl-eur-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ = "pearl_eur_price"
AVAIL_T = f"{PREFIX}/sensor/pearl_eur/availability"
STATE = f"{PREFIX}/sensor/{OBJ}/state"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"

DISCOVERY = {
    "name": "PEARL/EUR Price",
    "state_topic": STATE,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "unit_of_measurement": "EUR",
    "device_class": "monetary",
    "state_class": "measurement",
    "unique_id": OBJ,
    "device": DEVICE,
}
DISCOVERY["suggested_display_precision"] = 3

conn = http.client.HTTPSConnection("api.coingecko.com", timeout=15)
conn.connect()

def spot():
    conn.request("GET", "/api/v3/simple/price?ids=pearl-2&vs_currencies=eur&precision=full", headers={"User-Agent": "mqtt-sensors"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    return float(data["pearl-2"]["eur"])

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(CONFIG, json.dumps(DISCOVERY))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        client.publish(STATE, format(spot(), ".15g"), retain=True)  # retained: slow interval
        time.sleep(60.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    conn.close()
