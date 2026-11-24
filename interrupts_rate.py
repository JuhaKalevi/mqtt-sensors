#!/usr/bin/env python3
"""Interrupt rates → MQTT + Home Assistant discovery (/proc/interrupts, 1 s)."""
import json, os, re, time
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client, slot_id
)

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device()
CLIENT_ID = f"interrupts-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/{slot_id('interrupts')}/availability"
TITLES = {
    "total": "Total Interrupt Rate",
    "max_cpu": "Max CPU Interrupt Rate",
    "LOC": "Local Timer Interrupt Rate",
    "RES": "Rescheduling Interrupt Rate",
    "CAL": "Function Call Interrupt Rate",
    "TLB": "TLB Shootdown Rate",
    "IWI": "IRQ Work Interrupt Rate",
}

SENSORS = {}
for key, title in TITLES.items():
    unique = slot_id(f"interrupts_{key.lower()}")
    state = f"{PREFIX}/sensor/{unique}/state"
    extra = {"suggested_display_precision": 1, "entity_category": "diagnostic"}
    if key in ("total", "max_cpu"):
        extra["json_attributes_topic"] = f"{PREFIX}/sensor/{unique}/attributes"
    SENSORS[key] = (state, f"{PREFIX}/sensor/{unique}/config",
                    make_sensor_discovery(title, state, AVAIL_T, unique, DEVICE, "irq/s", None, "measurement", **extra))

def irq_source(desc):
    tok = desc.split()[-1]
    tok = re.sub(r"q\d+$", "", tok)
    return re.split(r"[-.]", tok, 1)[0]

def parse(text):
    lines = text.splitlines()
    cpus = [int(c[3:]) for c in lines[0].split() if c.startswith("CPU")]
    n = len(cpus)
    rows = {}
    devices = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        parts = rest.split()
        counts = []
        i = 0
        while i < len(parts) and parts[i].isdigit():
            counts.append(int(parts[i]))
            i += 1
        desc = " ".join(parts[i:])
        per_cpu = counts if len(counts) == n else None
        total = sum(counts)
        rows[name] = (per_cpu, total)
        if name.isdigit() and desc:
            src = irq_source(desc)
            devices[src] = devices.get(src, 0) + total
    return cpus, rows, devices

def line_deltas(prev_cpus, prev_rows, cpus, rows):
    prev_idx = {c: i for i, c in enumerate(prev_cpus)}
    per_cpu = {c: 0 for c in cpus}
    totals = {}
    for name, (counts, total) in rows.items():
        prev = prev_rows.get(name)
        if not prev:
            continue
        prev_counts, prev_total = prev
        if counts is not None and prev_counts is not None:
            d = 0
            for i, cpu in enumerate(cpus):
                j = prev_idx.get(cpu)
                if j is None or j >= len(prev_counts):
                    continue
                if counts[i] >= prev_counts[j]:
                    inc = counts[i] - prev_counts[j]
                    d += inc
                    per_cpu[cpu] += inc
            totals[name] = d
        elif total >= prev_total:
            totals[name] = total - prev_total
    return per_cpu, totals

def device_deltas(prev, devices):
    out = {}
    for src, total in devices.items():
        if src in prev and total >= prev[src]:
            out[src] = total - prev[src]
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

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        for _, config, disc in SENSORS.values():
            client.publish(config, json.dumps(disc))
        client.publish(AVAIL_T, "online")

fd = os.open("/proc/interrupts", os.O_RDONLY)
client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

prev_cpus, prev_rows, prev_devices = parse(read_proc(fd))
t_prev = time.monotonic()
try:
    while True:
        time.sleep(1.0)
        t_now = time.monotonic()
        dt = max(t_now - t_prev, 0.001)
        t_prev = t_now
        cpus, rows, devices = parse(read_proc(fd))
        per_cpu, totals = line_deltas(prev_cpus, prev_rows, cpus, rows)
        src_rates = {k: v / dt for k, v in device_deltas(prev_devices, devices).items() if v}
        prev_cpus, prev_rows, prev_devices = cpus, rows, devices
        total = sum(totals.values()) / dt
        client.publish(SENSORS["total"][0], f"{total:.1f}")
        top = dict(sorted(src_rates.items(), key=lambda x: -x[1])[:8])
        client.publish(SENSORS["total"][2]["json_attributes_topic"],
                       json.dumps({k: round(v, 1) for k, v in top.items()}))
        if per_cpu:
            cpu, val = max(per_cpu.items(), key=lambda x: x[1])
            client.publish(SENSORS["max_cpu"][0], f"{val / dt:.1f}")
            client.publish(SENSORS["max_cpu"][2]["json_attributes_topic"], json.dumps({"cpu": cpu}))
        for key in ("LOC", "RES", "CAL", "TLB", "IWI"):
            client.publish(SENSORS[key][0], f"{totals.get(key, 0) / dt:.1f}")
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
