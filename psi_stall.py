#!/usr/bin/env python3
"""Pressure stall info → MQTT + Home Assistant discovery (/proc/pressure, 1 s)."""
import json, os, time
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client, slot_id
)

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device()
CLIENT_ID = f"psi-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/{slot_id('psi')}/availability"
RESOURCES = (("cpu", "some", "CPU Some Stall"),
             ("memory", "some", "Memory Some Stall"),
             ("memory", "full", "Memory Full Stall"),
             ("io", "some", "I/O Some Stall"),
             ("io", "full", "I/O Full Stall"))

def parse(text):
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        fields = {}
        for item in parts[1:]:
            k, _, v = item.partition("=")
            fields[k] = float(v) if k.startswith("avg") else int(v)
        out[parts[0]] = fields
    return out

def read_proc(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        b = os.read(fd, 65536)
        if not b:
            break
        chunks.append(b)
    return b"".join(chunks).decode()

fds = {}
for res, kind, title in RESOURCES:
    if res in fds:
        continue
    path = f"/proc/pressure/{res}"
    try:
        fds[res] = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        pass
if not fds:
    raise SystemExit("no /proc/pressure")

SENSORS = {}
for res, kind, title in RESOURCES:
    if res not in fds:
        continue
    unique = slot_id(f"psi_{res}_{kind}")
    state = f"{PREFIX}/sensor/{unique}/state"
    disc = make_sensor_discovery(title, state, AVAIL_T, unique, DEVICE, "%", None, "measurement",
                                 suggested_display_precision=2,
                                 json_attributes_topic=f"{PREFIX}/sensor/{unique}/attributes",
                                 entity_category="diagnostic")
    SENSORS[(res, kind)] = (state, f"{PREFIX}/sensor/{unique}/config", disc)
if not SENSORS:
    raise SystemExit("no /proc/pressure")

def snapshot():
    data = {}
    for res, fd in fds.items():
        data[res] = parse(read_proc(fd))
    return data

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

prev = snapshot()
t_prev = time.monotonic()
try:
    while True:
        time.sleep(1.0)
        t_now = time.monotonic()
        dt = max(t_now - t_prev, 0.001)
        t_prev = t_now
        curr = snapshot()
        for (res, kind), (state, _, disc) in SENSORS.items():
            now = curr.get(res, {}).get(kind)
            was = prev.get(res, {}).get(kind)
            if not now:
                continue
            if was and now["total"] >= was["total"]:
                pct = 100.0 * (now["total"] - was["total"]) / (dt * 1e6)
            else:
                pct = 0.0
            client.publish(state, f"{pct:.2f}")
            client.publish(disc["json_attributes_topic"], json.dumps({"avg10": now.get("avg10", 0.0)}))
        prev = curr
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
