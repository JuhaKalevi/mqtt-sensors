#!/usr/bin/env python3
"""CPU package power → MQTT + Home Assistant discovery (RAPL/powercap).

One device per machine (named after the host). Designed so additional
entities (GPU power, etc.) can later join the same device.
"""

import time, glob, os, json, socket
import paho.mqtt.client as mqtt

def load_dotenv(path=".env"):
    """Minimal .env loader (no extra dependency)."""
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)

load_dotenv()

# ── identity ───────────────────────────────────────────────────────────────
HOSTNAME   = socket.gethostname().split(".")[0]
DEVICE_ID  = f"linux_host_{HOSTNAME}"          # stable device identifier
OBJECT     = f"cpu_package_power_{HOSTNAME}"   # unique entity id
CLIENT_ID  = f"cpu-power-{HOSTNAME}"           # unique MQTT client id
ENTITY_NAME = "CPU Package Power"              # short, clean entity name

# ── MQTT config (env / .env) ───────────────────────────────────────────────
HOST   = os.getenv("MQTT_HOST", "localhost")
PORT   = int(os.getenv("MQTT_PORT", "1883"))
USER   = os.getenv("MQTT_USER") or None
PASS   = os.getenv("MQTT_PASS") or None
PREFIX = os.getenv("MQTT_PREFIX", "homeassistant")

STATE_T  = f"{PREFIX}/sensor/{OBJECT}/state"
CONFIG_T = f"{PREFIX}/sensor/{OBJECT}/config"
AVAIL_T  = f"{PREFIX}/sensor/{OBJECT}/availability"

DISCOVERY = {
    "name": ENTITY_NAME,
    "state_topic": STATE_T,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "unit_of_measurement": "W",
    "device_class": "power",
    "state_class": "measurement",
    "unique_id": OBJECT,
    "device": {
        "identifiers": [DEVICE_ID],
        "name": HOSTNAME,                       # device = the computer
        "model": "Linux host",
        "manufacturer": "Linux",
    },
}

def find_pkg_energy():
    for d in glob.glob("/sys/class/powercap/intel-rapl:*"):
        try:
            if open(f"{d}/name").read().strip().startswith("package"):
                return open(f"{d}/energy_uj")
        except OSError:
            continue
    raise SystemExit("no package RAPL domain found")

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_T, json.dumps(DISCOVERY), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

# ── MQTT client ────────────────────────────────────────────────────────────
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
if USER:
    client.username_pw_set(USER, PASS)
client.will_set(AVAIL_T, "offline", qos=1, retain=True)
client.on_connect = on_connect
client.connect(HOST, PORT, keepalive=60)
client.loop_start()

# ── power loop ─────────────────────────────────────────────────────────────
f = find_pkg_energy()
prev = int(f.read())
f.seek(0)

try:
    while True:
        time.sleep(1.0)
        curr = int(f.read())
        f.seek(0)
        watts = (curr - prev) / 1e6
        prev = curr
        client.publish(STATE_T, f"{watts:.1f}", retain=True)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
