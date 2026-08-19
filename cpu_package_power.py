#!/usr/bin/python3 -Bu
"""CPU package power → MQTT + Home Assistant discovery (RAPL/powercap)."""
import time, glob, os, re
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, make_power_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
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
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
STATE_T = f"{PREFIX}/sensor/{OBJECT}/state"
CONFIG_T = f"{PREFIX}/sensor/{OBJECT}/config"
AVAIL_T = f"{PREFIX}/sensor/{OBJECT}/availability"

DISCOVERY = make_power_discovery(ENTITY_NAME, STATE_T, AVAIL_T, OBJECT, DEVICE)

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
        client.publish(CONFIG_T, __import__("json").dumps(DISCOVERY), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
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
