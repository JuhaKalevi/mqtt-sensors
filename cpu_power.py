#!/usr/bin/env python3
"""CPU package power → MQTT + Home Assistant discovery (RAPL/powercap)."""

import time, glob, os, json
import paho.mqtt.client as mqtt

# ── MQTT config (env vars, defaults are sensible) ──────────────────────────
HOST     = os.getenv("MQTT_HOST", "localhost")
PORT     = int(os.getenv("MQTT_PORT", "1883"))
USER     = os.getenv("MQTT_USER") or None
PASS     = os.getenv("MQTT_PASS") or None
PREFIX   = os.getenv("MQTT_PREFIX", "homeassistant")   # discovery prefix
OBJECT   = "cpu_package_power"                          # unique object_id
NAME     = "CPU Package Power"
STATE_T  = f"{PREFIX}/sensor/{OBJECT}/state"
CONFIG_T = f"{PREFIX}/sensor/{OBJECT}/config"
AVAIL_T  = f"{PREFIX}/sensor/{OBJECT}/availability"

DISCOVERY = {
    "name": NAME,
    "state_topic": STATE_T,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "unit_of_measurement": "W",
    "device_class": "power",
    "state_class": "measurement",
    "unique_id": OBJECT,
    "device": {
        "identifiers": ["linux_cpu_power"],
        "name": "CPU Power",
        "model": "RAPL",
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
client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cpu-power")
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
