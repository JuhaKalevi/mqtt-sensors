#!/usr/bin/env python3
"""XMRig hashrate, reject ratio, mining switch, optional profitability factor → MQTT + HA."""
import json, os, sys, time, http.client
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
HA_SPOT = HA_TOKEN is not None and SPOT_TOPIC is None and SPOT_FIXED is None
PROFIT_WANTED = SPOT_TOPIC is not None or SPOT_FIXED is not None or HA_TOKEN is not None

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
TOPIC_RATE = f"{PREFIX}/sensor/xmr_per_hs_day/state"
TOPIC_EUR = f"{PREFIX}/sensor/xmr_eur_price/state"

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
last_xmrig_print = 0.0

def auth_headers():
    h = {"Content-Type": "application/json"}
    if XMRIG_TOKEN:
        h["Authorization"] = f"Bearer {XMRIG_TOKEN}"
    return h

conn = http.client.HTTPConnection(XMRIG_HOST, XMRIG_PORT, timeout=5)
conn.connect()

ha_conn = None

def ensure_ha_conn():
    global ha_conn
    if ha_conn is not None:
        return ha_conn
    ha = urlparse(HA_URL)
    ha_host = ha.hostname or "127.0.0.1"
    ha_port = ha.port or (443 if ha.scheme == "https" else 80)
    if ha.scheme == "https":
        ha_conn = http.client.HTTPSConnection(ha_host, ha_port, timeout=5)
    else:
        ha_conn = http.client.HTTPConnection(ha_host, ha_port, timeout=5)
    ha_conn.connect()
    return ha_conn

def close_ha_conn():
    global ha_conn
    if ha_conn is None:
        return
    try:
        ha_conn.close()
    except Exception:
        pass
    ha_conn = None

def api(method, path, body=None):
    payload = None if body is None else json.dumps(body).encode()
    conn.request(method, path, body=payload, headers=auth_headers())
    resp = conn.getresponse()
    raw = resp.read()
    data = json.loads(raw) if raw else {}
    if resp.status >= 400:
        data["_http_status"] = resp.status
    return data

def summary():
    return api("GET", "/2/summary")

def rpc(method):
    api("POST", "/json_rpc", {"method": method, "id": 1})

def refresh_ha_spot():
    global spot
    try:
        c = ensure_ha_conn()
        c.request(
            "GET",
            f"/api/states/{HA_SPOT_ENTITY}",
            headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
        )
        resp = c.getresponse()
        body = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"HA {HA_SPOT_ENTITY} HTTP {resp.status}: {body[:200]!r}")
        data = json.loads(body)
        spot = float(data["state"])
    except Exception:
        close_ha_conn()
        raise

def inputs_ready():
    return (
        PROFIT_WANTED
        and xmr_rate is not None
        and xmr_eur is not None
        and spot is not None
        and spot > 0
        and power_w is not None
    )

def missing_inputs(eff_hr, eff_power):
    missing = []
    if not PROFIT_WANTED:
        missing.append("spot source (set HA_TOKEN, ELECTRICITY_EUR_PER_KWH_TOPIC, or ELECTRICITY_EUR_PER_KWH)")
        return missing
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
        else:
            missing.append("ELECTRICITY_EUR_PER_KWH")
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
    global last_hr, last_power, pf_discovered, last_ha_spot, last_missing_print, last_xmrig_print
    if "hashrate" not in data or "results" not in data:
        now = time.monotonic()
        if now - last_xmrig_print >= 60.0:
            status = data.get("_http_status")
            keys = sorted(k for k in data if not str(k).startswith("_"))
            print(
                f"xmrig_status: XMRig summary missing hashrate/results "
                f"http={status} keys={keys} body={data!r}"[:500],
                flush=True,
            )
            last_xmrig_print = now
        return
    total = data["hashrate"]["total"]
    hr = float(total[1] if len(total) > 1 and total[1] is not None else (total[0] or 0))
    good = int(data["results"]["shares_good"])
    shares = int(data["results"]["shares_total"])
    rej = 0.0 if shares == 0 else (shares - good) * 100.0 / shares
    paused = bool(data["paused"])
    client.publish(STATE_HR, format(hr, ".15g"), retain=True)
    client.publish(STATE_REJ, format(rej, ".15g"), retain=True)
    client.publish(STATE_SW, "OFF" if paused else "ON", retain=True)
    if not PROFIT_WANTED:
        return
    now = time.monotonic()
    if HA_SPOT and now - last_ha_spot >= 60.0:
        try:
            refresh_ha_spot()
        except Exception as err:
            print(f"xmrig_status: HA spot refresh failed: {err}", flush=True)
        last_ha_spot = now
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
        if PROFIT_WANTED:
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
    if not PROFIT_WANTED:
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
    f"xmrig_status: host={HOSTNAME} profit_wanted={PROFIT_WANTED} "
    f"ha_spot={HA_SPOT} ha_url={HA_URL if HA_SPOT else '-'} power_topic={TOPIC_POWER}",
    flush=True,
)
if not PROFIT_WANTED:
    print("xmrig_status: basic stats only (no HA_TOKEN / spot topic / fixed EUR per kWh)", flush=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.on_message = on_message
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        try:
            publish_stats(summary())
        except Exception as err:
            now = time.monotonic()
            if now - last_xmrig_print >= 60.0:
                print(f"xmrig_status: XMRig poll failed: {err}", flush=True)
                last_xmrig_print = now
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
