#!/usr/bin/env python3
"""Per-collector CPU and context-switch rates → MQTT + Home Assistant discovery (/proc, 1 s)."""
import json, os, time
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client, slot_id
)

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device()
CLIENT_ID = f"collector-cost-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/{slot_id('collector_cost')}/availability"
CLK_TCK = os.sysconf("SC_CLK_TCK") or 100
ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = {f for f in os.listdir(ROOT) if f.endswith(".py")}
support = os.path.join(ROOT, "support")
if os.path.isdir(support):
    SCRIPTS.update(f for f in os.listdir(support) if f.endswith(".py"))

TITLES = (
    ("cpu", "Collectors CPU", "%", 2),
    ("voluntary", "Collectors Voluntary Context Switch Rate", "sw/s", 1),
    ("nonvoluntary", "Collectors Nonvoluntary Context Switch Rate", "sw/s", 1),
)

SENSORS = {}
for key, title, unit, prec in TITLES:
    unique = slot_id(f"collector_cost_{key}")
    state = f"{PREFIX}/sensor/{unique}/state"
    extra = {"suggested_display_precision": prec, "entity_category": "diagnostic"}
    if key == "cpu":
        extra["json_attributes_topic"] = f"{PREFIX}/sensor/{unique}/attributes"
    SENSORS[key] = (state, f"{PREFIX}/sensor/{unique}/config",
                    make_sensor_discovery(title, state, AVAIL_T, unique, DEVICE, unit, None, "measurement", **extra))

def read_collectors():
    found = {}
    for fn in os.listdir("/proc"):
        if not fn.isdigit():
            continue
        path = f"/proc/{fn}"
        try:
            with open(f"{path}/cmdline", "rb") as f:
                parts = [p.decode(errors="replace") for p in f.read().split(b"\0") if p]
            if not parts:
                continue
            argv0 = os.path.basename(parts[0])
            name = next((os.path.basename(p) for p in parts if os.path.basename(p) in SCRIPTS), None)
            if name is None or ("python" not in argv0 and argv0 not in SCRIPTS):
                continue
            with open(f"{path}/stat") as f:
                stat = f.read()
            with open(f"{path}/status") as f:
                status = f.read()
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue
        fields = stat[stat.rfind(")") + 2:].split()
        ticks = int(fields[11]) + int(fields[12])
        vol = nonvol = 0
        for line in status.splitlines():
            if line.startswith("voluntary_ctxt_switches:"):
                vol = int(line.split()[1])
            elif line.startswith("nonvoluntary_ctxt_switches:"):
                nonvol = int(line.split()[1])
        key = name[:-3] if name.endswith(".py") else name
        if key in found:
            key = f"{key}_{fn}"
        found[key] = (ticks, vol, nonvol)
    return found

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        for _, config, disc in SENSORS.values():
            client.publish(config, json.dumps(disc))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

prev = read_collectors()
t_prev = time.monotonic()
try:
    while True:
        time.sleep(1.0)
        t_now = time.monotonic()
        dt = max(t_now - t_prev, 0.001)
        t_prev = t_now
        curr = read_collectors()
        rates = {}
        total_cpu = total_vol = total_nonvol = 0.0
        for name, (ticks, vol, nonvol) in curr.items():
            was = prev.get(name)
            if not was or ticks < was[0] or vol < was[1] or nonvol < was[2]:
                continue
            cpu = 100.0 * ((ticks - was[0]) / CLK_TCK) / dt
            vrate = (vol - was[1]) / dt
            nrate = (nonvol - was[2]) / dt
            rates[name] = (cpu, vrate, nrate)
            total_cpu += cpu
            total_vol += vrate
            total_nonvol += nrate
        prev = curr
        client.publish(SENSORS["cpu"][0], f"{total_cpu:.2f}")
        client.publish(SENSORS["voluntary"][0], f"{total_vol:.1f}")
        client.publish(SENSORS["nonvoluntary"][0], f"{total_nonvol:.1f}")
        attrs = {"count": len(rates)}
        if rates:
            attrs["max"] = max(rates, key=lambda n: rates[n][0])
        for name, (cpu, vrate, nrate) in sorted(rates.items(), key=lambda x: -x[1][0]):
            attrs[f"{name}_cpu"] = round(cpu, 2)
            attrs[f"{name}_voluntary"] = round(vrate, 1)
            attrs[f"{name}_nonvoluntary"] = round(nrate, 1)
        client.publish(SENSORS["cpu"][2]["json_attributes_topic"], json.dumps(attrs))
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
