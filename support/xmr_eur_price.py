#!/usr/bin/env python3
"""XMR/EUR spot price → MQTT + Home Assistant discovery (Kraken public ticker)."""
import json, sys, time, http.client
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mqtt_common import get_hostname, get_mqtt_settings, create_client

HOSTNAME = get_hostname()
DEVICE_ID = f"xmr_eur_price_{HOSTNAME}"
DEVICE = {"identifiers": [DEVICE_ID], "name": "XMR/EUR"}
CLIENT_ID = f"xmr-eur-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ = f"xmr_eur_price_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/xmr_eur_{HOSTNAME}/availability"
STATE = f"{PREFIX}/sensor/{OBJ}/state"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"

DISCOVERY = {
    "name": "XMR/EUR Price",
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
DISCOVERY["suggested_display_precision"] = 2

conn = http.client.HTTPSConnection("api.kraken.com", timeout=15)
conn.connect()

def spot():
    conn.request("GET", "/0/public/Ticker?pair=XMREUR")
    resp = conn.getresponse()
    data = json.loads(resp.read())
    pair = next(iter(data["result"].values()))
    return float(pair["c"][0])

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG, json.dumps(DISCOVERY), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        client.publish(STATE, format(spot(), ".15g"), retain=True)
        time.sleep(60.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    conn.close()
