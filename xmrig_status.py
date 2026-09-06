#!/usr/bin/env python3
"""XMRig hashrate, reject ratio, mining switch, profitability factor → MQTT + HA."""
import json, os, sys, time, http.client
from datetime import datetime, timezone
from urllib.parse import urlparse
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
HA_URL = os.getenv("HA_URL", "http://127.0.0.1:8123").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN") or None
HA_SPOT_ENTITY = os.getenv("HA_ELECTRICITY_ENTITY", "sensor.porssisahko_electricity_price")
if SPOT_FIXED is not None:
    SPOT_SOURCE = "fixed"
elif SPOT_TOPIC is not None:
    SPOT_SOURCE = "mqtt"
elif HA_TOKEN is not None:
    SPOT_SOURCE = "ha"
else:
    SPOT_SOURCE = "porssisahko"
HA_SPOT = SPOT_SOURCE == "ha"
PORSSI_SPOT = SPOT_SOURCE == "porssisahko"
PORSSI_MIN_INTERVAL = 3600.0
PORSSI_MIN_FUTURE = 12 * 3600.0

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
pf_discovered = False
last_ha_spot = 0.0
last_missing_print = 0.0
porssi_prices = []
last_porssi_fetch = 0.0

def auth_headers():
    h = {"Content-Type": "application/json"}
    if XMRIG_TOKEN:
        h["Authorization"] = f"Bearer {XMRIG_TOKEN}"
    return h

conn = http.client.HTTPConnection(XMRIG_HOST, XMRIG_PORT, timeout=5)
conn.connect()

ha_conn = None
if HA_SPOT:
    ha = urlparse(HA_URL)
    ha_host = ha.hostname or "127.0.0.1"
    ha_port = ha.port or (443 if ha.scheme == "https" else 80)
    if ha.scheme == "https":
        ha_conn = http.client.HTTPSConnection(ha_host, ha_port, timeout=5)
    else:
        ha_conn = http.client.HTTPConnection(ha_host, ha_port, timeout=5)
    ha_conn.connect()

porssi_conn = None
if PORSSI_SPOT:
    porssi_conn = http.client.HTTPSConnection("api.porssisahko.net", timeout=15)
    porssi_conn.connect()

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

def parse_iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def refresh_ha_spot():
    global spot
    ha_conn.request(
        "GET",
        f"/api/states/{HA_SPOT_ENTITY}",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
    )
    resp = ha_conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise RuntimeError(f"HA {HA_SPOT_ENTITY} HTTP {resp.status}: {body[:200]!r}")
    data = json.loads(body)
    spot = float(data["state"])

def porssi_lookup(now_utc):
    current = None
    latest_end = None
    for start, end, eur in porssi_prices:
        if latest_end is None or end > latest_end:
            latest_end = end
        if start <= now_utc <= end:
            current = eur
    coverage = (latest_end - now_utc).total_seconds() if latest_end is not None else 0.0
    return current, coverage

def fetch_porssi():
    global porssi_prices, last_porssi_fetch, spot
    porssi_conn.request("GET", "/v2/latest-prices.json")
    resp = porssi_conn.getresponse()
    body = resp.read()
    if resp.status != 200:
        raise RuntimeError(f"porssisahko HTTP {resp.status}: {body[:200]!r}")
    data = json.loads(body)
    rows = []
    for item in data["prices"]:
        start = parse_iso(item["startDate"])
        end = parse_iso(item["endDate"])
        eur = float(item["price"]) / 100.0
        rows.append((start, end, eur))
    porssi_prices = rows
    last_porssi_fetch = time.monotonic()
    now_utc = datetime.now(timezone.utc)
    current, coverage = porssi_lookup(now_utc)
    if current is not None:
        spot = current
    print(
        f"xmrig_status: porssisahko refresh slots={len(rows)} "
        f"current={current if current is not None else 'none'} "
        f"future_h={coverage / 3600.0:.1f}",
        flush=True,
    )

def refresh_porssi_spot():
    global spot
    now_utc = datetime.now(timezone.utc)
    current, coverage = porssi_lookup(now_utc)
    need = current is None or coverage <= PORSSI_MIN_FUTURE
    if need and (last_porssi_fetch == 0.0 or time.monotonic() - last_porssi_fetch >= PORSSI_MIN_INTERVAL):
        fetch_porssi()
        current, coverage = porssi_lookup(datetime.now(timezone.utc))
    spot = current

def inputs_ready():
    return (
        xmr_rate is not None
        and xmr_eur is not None
        and spot is not None
        and spot > 0
        and power_w is not None
    )

def missing_inputs(eff_hr, eff_power):
    missing = []
    if xmr_rate is None:
        missing.append(f"mqtt {TOPIC_RATE}")
    if xmr_eur is None:
        missing.append(f"mqtt {TOPIC_EUR}")
    if power_w is None:
        missing.append(f"mqtt {TOPIC_POWER}")
    if spot is None or spot <= 0:
        if HA_SPOT:
            missing.append(f"ha {HA_SPOT_ENTITY} via {HA_URL}")
        elif SPOT_TOPIC:
            missing.append(f"mqtt {SPOT_TOPIC}")
        elif SPOT_FIXED is not None:
            missing.append("ELECTRICITY_EUR_PER_KWH")
        else:
            missing.append("porssisahko api.porssisahko.net/v2/latest-prices.json")
    if eff_hr <= 0:
        missing.append("hashrate (live or last mining)")
    if eff_power <= 0:
        missing.append("package watts (live or last mining)")
    return missing

def profitability(eff_hr, eff_power):
    if not inputs_ready() or eff_hr <= 0 or eff_power <= 0:
        return None
    revenue = eff_hr * xmr_rate / 24.0 * xmr_eur
    cost = eff_power / 1000.0 * spot
    return revenue / cost

def publish_stats(data):
    global last_hr, last_power, pf_discovered, last_ha_spot, last_missing_print
    total = data["hashrate"]["total"]
    hr = float(total[1] if len(total) > 1 and total[1] is not None else (total[0] or 0))
    good = int(data["results"]["shares_good"])
    shares = int(data["results"]["shares_total"])
    rej = 0.0 if shares == 0 else (shares - good) * 100.0 / shares
    paused = bool(data["paused"])
    client.publish(STATE_HR, format(hr, ".15g"), retain=True)
    client.publish(STATE_REJ, format(rej, ".15g"), retain=True)
    client.publish(STATE_SW, "OFF" if paused else "ON", retain=True)
    now = time.monotonic()
    if HA_SPOT and now - last_ha_spot >= 60.0:
        try:
            refresh_ha_spot()
        except Exception as err:
            print(f"xmrig_status: HA spot refresh failed: {err}", flush=True)
        last_ha_spot = now
    elif PORSSI_SPOT:
        try:
            refresh_porssi_spot()
        except Exception as err:
            print(f"xmrig_status: porssisahko refresh failed: {err}", flush=True)
    if not paused and hr > 0:
        last_hr = hr
    if not paused and power_w is not None and power_w > 0:
        last_power = power_w
    eff_hr = hr if not paused and hr > 0 else last_hr
    eff_power = power_w if not paused and power_w is not None and power_w > 0 else last_power
    factor = profitability(eff_hr, eff_power)
    if factor is None:
        missing = missing_inputs(eff_hr, eff_power)
        if missing and now - last_missing_print >= 60.0:
            print(f"xmrig_status: profitability waiting on: {', '.join(missing)}", flush=True)
            last_missing_print = now
        return
    if not pf_discovered:
        client.publish(CONFIG_PF, json.dumps(DISCOVERY_PF), retain=True)
        pf_discovered = True
        print(f"xmrig_status: profitability factor online (value {factor:.6g})", flush=True)
    client.publish(STATE_PF, format(factor, ".15g"), retain=True)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_HR, json.dumps(DISCOVERY_HR), retain=True)
        client.publish(CONFIG_REJ, json.dumps(DISCOVERY_REJ), retain=True)
        client.publish(CONFIG_SW, json.dumps(DISCOVERY_SW), retain=True)
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

print(
    f"xmrig_status: host={HOSTNAME} spot={SPOT_SOURCE} power_topic={TOPIC_POWER}",
    flush=True,
)

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
    if ha_conn is not None:
        ha_conn.close()
    if porssi_conn is not None:
        porssi_conn.close()
