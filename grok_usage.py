#!/usr/bin/env python3
"""Grok subscription credit usage → MQTT + Home Assistant discovery (Grok CLI billing endpoint, token from the CLI's auth.json)."""
import json, os, time, http.client
from mqtt_common import get_hostname, get_mqtt_settings, create_client, make_sensor_discovery

HOSTNAME = get_hostname()
DEVICE = {"identifiers": ["grok"], "name": "Grok"}
CLIENT_ID = f"grok-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AUTH_PATH = os.environ["GROK_AUTH_PATH"]
INTERVAL = float(os.environ["GROK_USAGE_INTERVAL"])

OBJ = "grok_usage"
AVAIL_T = f"{PREFIX}/sensor/grok/availability"
STATE = f"{PREFIX}/sensor/{OBJ}/state"
ATTRS = f"{PREFIX}/sensor/{OBJ}/attributes"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"
DISCOVERY = make_sensor_discovery("Grok Usage", STATE, AVAIL_T, OBJ, DEVICE,
                                  unit="%", device_class=None, state_class="measurement")
DISCOVERY["json_attributes_topic"] = ATTRS

conn = http.client.HTTPSConnection("cli-chat-proxy.grok.com", timeout=15)
conn.connect()

def token():
    with open(AUTH_PATH) as f:
        auth = json.load(f)
    return next(v["key"] for k, v in auth.items() if k.startswith("https://auth.x.ai::"))

def usage():
    conn.request("GET", "/v1/billing?format=credits", headers={
        "Authorization": f"Bearer {token()}",
        "x-xai-token-auth": "xai-grok-cli",
        "Accept": "application/json",
    })
    resp = conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise SystemExit(f"grok_usage: HTTP {resp.status} {body.decode(errors='replace')}")
    return json.loads(body)["config"]

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
        cfg = usage()
        attrs = {"reset": cfg["currentPeriod"]["end"], "period": cfg["currentPeriod"]["type"]}
        attrs.update({p["product"]: p.get("usagePercent", 0.0) for p in cfg.get("productUsage", [])})
        client.publish(ATTRS, json.dumps(attrs), retain=True)  # retained: slow interval
        client.publish(STATE, str(cfg.get("creditUsagePercent", 0.0)), retain=True)  # retained: slow interval
        time.sleep(INTERVAL)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    conn.close()
