#!/usr/bin/env python3
"""CPU package power + energy → MQTT + Home Assistant discovery (RAPL/powercap)."""
import time, glob, os, re, json
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_power_discovery, make_energy_discovery, create_client
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
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

STATE_POWER = f"{PREFIX}/sensor/{OBJECT}/state"
STATE_ENERGY = f"{PREFIX}/sensor/{OBJECT}_energy/state"
CONFIG_POWER = f"{PREFIX}/sensor/{OBJECT}/config"
CONFIG_ENERGY = f"{PREFIX}/sensor/{OBJECT}_energy/config"
AVAIL_T = f"{PREFIX}/sensor/{OBJECT}/availability"

DISCOVERY_POWER = make_power_discovery(
    f"{CPU_MODEL} Package Power", STATE_POWER, AVAIL_T, OBJECT, DEVICE
)
DISCOVERY_ENERGY = make_energy_discovery(
    f"{CPU_MODEL} Package Energy", STATE_ENERGY, AVAIL_T, f"{OBJECT}_energy", DEVICE
)

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
        client.publish(CONFIG_POWER, json.dumps(DISCOVERY_POWER), retain=True)
        client.publish(CONFIG_ENERGY, json.dumps(DISCOVERY_ENERGY), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

f = find_pkg_energy()
prev = int(f.read())
f.seek(0)
energy_wh = 0.0
t_prev = time.monotonic()

try:
    while True:
        time.sleep(1.0)
        t_now = time.monotonic()
        dt = t_now - t_prev
        t_prev = t_now

        curr = int(f.read())
        f.seek(0)
        delta = curr - prev
        if delta < 0:
            delta += 1 << 32
        prev = curr

        power = (delta / 1e6) / dt          # exact watts from measured interval
        energy_wh += power * (dt / 3600.0)

        client.publish(STATE_POWER, f"{power:.1f}", retain=True)
        client.publish(STATE_ENERGY, f"{energy_wh / 1000.0:.6f}", retain=True)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
