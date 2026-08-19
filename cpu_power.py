#!/usr/bin/env python3
"""CPU package power → MQTT + Home Assistant discovery (RAPL/powercap)."""

import time, glob, os, json, socket, re
import paho.mqtt.client as mqtt

def load_dotenv(path=".env"):
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

load_dotenv()

HOSTNAME = socket.gethostname().split(".")[0]
DEVICE_ID = f"linux_host_{HOSTNAME}"
OBJECT = f"cpu_package_power_{HOSTNAME}"
CLIENT_ID = f"cpu-power-{HOSTNAME}"

def get_cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    model = re.sub(r"\s+", " ", line.split(":", 1)[1].strip())
                    model = re.sub(r"\(R\)|\(TM\)|CPU @.*|with Radeon.*", "", model, flags=re.I).strip()
                    return model or "CPU"
    except OSError:
        pass
    return "CPU"

CPU_MODEL = get_cpu_model()
ENTITY_NAME = f"{CPU_MODEL} Package Power"

HOST = os.getenv("MQTT_HOST", "localhost")
PORT = int(os.getenv("MQTT_PORT", "1883"))
USER = os.getenv("MQTT_USER") or None
PASS = os.getenv("MQTT_PASS") or None
PREFIX = os.getenv("MQTT_PREFIX", "homeassistant")

STATE_T = f"{PREFIX}/sensor/{OBJECT}/state"
CONFIG_T = f"{PREFIX}/sensor/{OBJECT}/config"
AVAIL_T = f"{PREFIX}/sensor/{OBJECT}/availability"

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
        "name": HOSTNAME,
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

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID)
if USER:
    client.username_pw_set(USER, PASS)
client.will_set(AVAIL_T, "offline", qos=1, retain=True)
client.on_connect = on_connect
client.connect(HOST, PORT, keepalive=60)
client.loop_start()

f = find_pkg_energy()
prev = int(f.read())
f.seek(0)

try:
    while True:
        time.sleep(1.0)
        curr = int(f.read())
        f.seek(0)
        delta = curr - prev
        if delta < 0:
            delta += 1 << 32
        prev = curr
        client.publish(STATE_T, f"{delta / 1e6:.1f}", retain=True)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
