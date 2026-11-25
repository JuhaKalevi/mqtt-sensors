#!/usr/bin/env python3
"""PEARL/USDT market price → MQTT + Home Assistant discovery (Gate public spot ticker, NEAR Intents 1Click token prices)."""
import json, os, sys, time, http.client
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.mqtt_common import get_hostname, get_mqtt_settings, create_client, make_sensor_discovery

HOSTNAME = get_hostname()
DEVICE_ID = "pearl_usdt_price"
DEVICE = {"identifiers": [DEVICE_ID], "name": "PEARL/USDT", "manufacturer": "Gate / NEAR Intents", "model": "Price"}
CLIENT_ID = f"pearl-usdt-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ = "pearl_usdt_price"
AVAIL_T = f"{PREFIX}/sensor/pearl_usdt/availability"
ATTRS = f"{PREFIX}/sensor/{OBJ}/attributes"

def sensor(name, obj, **extra):
    return make_sensor_discovery(name, f"{PREFIX}/sensor/{obj}/state", AVAIL_T, obj, DEVICE, unit="USDT", state_class="measurement",
                                 suggested_display_precision=4, **extra)

DISCOVERY = sensor("Price", OBJ, json_attributes_topic=ATTRS)

gate_conn = http.client.HTTPSConnection("api.gateio.ws", timeout=15)

def gate():
    gate_conn.request("GET", "/api/v4/spot/tickers?currency_pair=PEARL_USDT")
    resp = gate_conn.getresponse()
    data = json.loads(resp.read())
    return float(data[0]["last"])

near_intents_conn = http.client.HTTPSConnection("1click.chaindefuser.com", timeout=15)

def near_intents():
    near_intents_conn.request("GET", "/v0/tokens", headers={"User-Agent": "mqtt-sensors"})
    resp = near_intents_conn.getresponse()
    usd = {t["assetId"]: float(t["price"]) for t in json.loads(resp.read())}
    return usd["nep141:prl.omft.near"] / usd["nep141:usdt.tether-token.near"]

SOURCES = {"gate": ("Gate Price", gate), "near_intents": ("NEAR Intents Price", near_intents)}

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(f"{PREFIX}/sensor/{OBJ}/config", json.dumps(DISCOVERY))
        for src, (name, _) in SOURCES.items():
            obj = f"pearl_usdt_{src}_price"
            client.publish(f"{PREFIX}/sensor/{obj}/config", json.dumps(sensor(name, obj)))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T, replay_states=True)
SLIPPAGE_PCT = float(os.environ["PEARL_USDT_SLIPPAGE_PCT"])
MODES = {"avg": lambda v: sum(v) / len(v), "max": max, "min": min}
MODE = os.getenv("PEARL_USDT_PRICE_MODE") or "avg"
if MODE not in MODES:
    sys.exit(f"PEARL_USDT_PRICE_MODE must be one of {', '.join(MODES)}, got {MODE!r}")
gate_conn.connect()
near_intents_conn.connect()
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        prices = []
        for src, (_, fetch) in SOURCES.items():
            price = fetch()
            prices.append(price)
            client.publish(f"{PREFIX}/sensor/pearl_usdt_{src}_price/state", format(price, ".15g"))
        client.publish(f"{PREFIX}/sensor/{OBJ}/state", format(MODES[MODE](prices), ".15g"))
        client.publish(ATTRS, json.dumps({"mode": MODE, "slippage_pct": SLIPPAGE_PCT}))
        time.sleep(60.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    gate_conn.close()
    near_intents_conn.close()
