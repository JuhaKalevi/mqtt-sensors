#!/usr/bin/env python3
"""NVIDIA GPU power + energy → MQTT + Home Assistant discovery (nvidia-smi --loop)."""
import time, subprocess, json, signal, os, select
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device,
    create_client, slot_id
)
from lib.nvidia_common import clean_gpu_name, gpu_entities

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device()
CLIENT_ID = f"gpu-power-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/{slot_id('gpu_power')}/availability"
ALL_STATS = [
    ("temperature.gpu", "temperature", "Temperature", "°C", "temperature"),
    ("temperature.memory", "memory_temperature", "Memory Temperature", "°C", "temperature"),
    ("fan.speed", "fan_speed", "Fan Speed", "%", None),
    ("clocks.gr", "core_clock", "Core Clock", "MHz", "frequency"),
    ("clocks.mem", "memory_clock", "Memory Clock", "MHz", "frequency"),
    ("utilization.gpu", "utilization", "Utilization", "%", None),
    ("utilization.memory", "memory_bandwidth", "Memory Bandwidth", "%", None),
    ("memory.used", "memory_used_percent", "Memory Used", "%", None),
    ("power.limit", "power_limit", "Power Limit", "W", "power"),
    ("pstate", "pstate", "Performance State", None, None),
    ("clocks_throttle_reasons.active", "throttle_reasons", "Throttle Reasons", None, None),
]

STATS = ALL_STATS if os.environ["NVIDIA_GPU_STATS"] else []
FIELDS = ",".join(["index", "name", "power.draw"] + [s[0] for s in STATS] + (["memory.total"] if STATS else []) + ["uuid"])

def stat_value(extra, i):
    if STATS[i][0] == "memory.used":
        return str(float(extra[i]) / float(extra[-1]) * 100)
    return extra[i]

def start_smi_stream():
    return subprocess.Popen(
        [
            "nvidia-smi",
            f"--query-gpu={FIELDS}",
            "--format=csv,noheader,nounits",
            "--loop=1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

def read_sample(proc, expected_count=None):
    lines = []
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            idx = int(parts[0])
            name = clean_gpu_name(parts[1])
            power = float(parts[2])
        except ValueError:
            power = 0.0
            name = clean_gpu_name(parts[1]) if len(parts) > 1 else "GPU"
            try:
                idx = int(parts[0])
            except ValueError:
                continue
        lines.append({"idx": idx, "name": name, "power": power, "uuid": parts[-1], "extra": parts[3:-1]})
        if expected_count is not None and len(lines) >= expected_count:
            break
        if expected_count is None and len(lines) > 0:
            if not select.select([proc.stdout], [], [], 0.05)[0]:
                break
    return lines

proc = start_smi_stream()
first = read_sample(proc)
if not first:
    proc.terminate()
    raise SystemExit("nvidia-smi produced no data (no GPUs or driver problem)")

gpus = []
for g in first:
    gpus.append(gpu_entities(PREFIX, AVAIL_T, g, STATS, DEVICE, len(first) > 1))

gpu_by_idx = {g["idx"]: g for g in gpus}
expected = len(gpus)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        for g in gpus:
            client.publish(g["config_p"], json.dumps(g["disc_p"]))
            client.publish(g["config_e"], json.dumps(g["disc_e"]))
            for _, _, config, disc in g["stats"]:
                client.publish(config, json.dumps(disc))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

def cleanup(*_):
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    os._exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

t_prev = time.monotonic()

try:
    # publish first sample
    t_now = time.monotonic()
    dt = max(t_now - t_prev, 0.001)
    t_prev = t_now
    for sample in first:
        g = gpu_by_idx.get(sample["idx"])
        if not g:
            continue
        power = sample["power"]
        g["energy_wh"] += power * (dt / 3600.0)
        client.publish(g["state_p"], f"{power:.1f}")
        client.publish(g["state_e"], f"{g['energy_wh'] / 1000.0:.6f}")
        for i, state, _, _ in g["stats"]:
            if not sample["extra"][i].startswith("["):
                client.publish(state, stat_value(sample["extra"], i))

    while True:
        sample = read_sample(proc, expected_count=expected)
        if sample is None:
            break
        t_now = time.monotonic()
        dt = max(t_now - t_prev, 0.001)
        t_prev = t_now
        for s in sample:
            g = gpu_by_idx.get(s["idx"])
            if not g:
                continue
            power = s["power"]
            g["energy_wh"] += power * (dt / 3600.0)
            client.publish(g["state_p"], f"{power:.1f}")
            client.publish(g["state_e"], f"{g['energy_wh'] / 1000.0:.6f}")
            for i, state, _, _ in g["stats"]:
                if not s["extra"][i].startswith("["):
                    client.publish(state, stat_value(s["extra"], i))
except Exception:
    pass
finally:
    cleanup()
