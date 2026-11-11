#!/usr/bin/env python3
"""USDT/EUR spot price → MQTT + Home Assistant discovery (Kraken public ticker)."""
import json, os, sys, time, http.client
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_common import get_hostname, get_mqtt_settings, create_client

HOSTNAME = get_hostname()
DEVICE_ID = "usdt_eur_price"
DEVICE = {"identifiers": [DEVICE_ID], "name": "USDT/EUR"}
CLIENT_ID = f"usdt-eur-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
SLIPPAGE_PCT = float(os.environ["USDT_EUR_SLIPPAGE_PCT"])

OBJ = "usdt_eur_price"
AVAIL_T = f"{PREFIX}/sensor/usdt_eur/availability"
STATE = f"{PREFIX}/sensor/{OBJ}/state"
ATTRS = f"{PREFIX}/sensor/{OBJ}/attributes"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"

DISCOVERY = {
    "name": "USDT/EUR Price",
    "state_topic": STATE,
    "json_attributes_topic": ATTRS,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "unit_of_measurement": "EUR",
    "device_class": "monetary",
    "state_class": "measurement",
    "unique_id": OBJ,
    "device": DEVICE,
}
DISCOVERY["suggested_display_precision"] = 4

conn = http.client.HTTPSConnection("api.kraken.com", timeout=15)
conn.connect()

def spot():
    conn.request("GET", "/0/public/Ticker?pair=USDTEUR")
    resp = conn.getresponse()
    data = json.loads(resp.read())
    pair = next(iter(data["result"].values()))
    return float(pair["c"][0])

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
        client.publish(STATE, format(spot(), ".15g"))
        client.publish(ATTRS, json.dumps({"slippage_pct": SLIPPAGE_PCT}))
        time.sleep(60.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    conn.close()
