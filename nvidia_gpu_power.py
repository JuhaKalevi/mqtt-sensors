#!/usr/bin/env python3
"""NVIDIA GPU power + energy → MQTT + Home Assistant discovery (nvidia-smi --loop). No select(); N lines per sample."""
import time, subprocess, json, signal, os, traceback
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device,
    create_client, slot_id
)
from lib.nvidia_common import (
    gpu_entities, gpu_discovery, stall_fields, stat_value, publish_gpu_extras, parse_line, read_n, no_data
)

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device()
CLIENT_ID = f"gpu-power-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/{slot_id('gpu_power')}/availability"
ALL_STATS = [
    ("temperature.gpu", "temperature", "Temperature", "°C", "temperature"),
    ("fan.speed", "fan_speed", "Fan Speed", "%", None),
    ("clocks.gr", "core_clock", "Core Clock", "MHz", "frequency"),
    ("clocks.mem", "memory_clock", "Memory Clock", "MHz", "frequency"),
    ("utilization.gpu", "utilization", "Utilization", "%", None),
    ("utilization.memory", "memory_bandwidth", "Memory Bandwidth", "%", None),
    ("memory.used", "memory_used_percent", "Memory Used", "%", None),
    ("power.limit", "power_limit", "Power Limit", "W", "power"),
    ("pstate", "pstate", "Performance State", None, None),
    ("clocks_event_reasons.active", "throttle_reasons", "Throttle Reasons", None, None),
]

STATS = ALL_STATS if os.environ["NVIDIA_GPU_STATS"] else []
FIELDS = ",".join(["index", "name", "power.draw"] + [s[0] for s in STATS] + stall_fields(STATS) + (["memory.total"] if STATS else []) + ["uuid"])

QUERY = ["nvidia-smi", f"--query-gpu={FIELDS}", "--format=csv,noheader,nounits"]

# One plain query first: the GPU count for read_n() and, on failure, nvidia-smi's own message and exit code.
listed = subprocess.run(QUERY, capture_output=True, text=True, stdin=subprocess.DEVNULL)
first = [r for r in map(parse_line, listed.stdout.splitlines()) if r]
if not first:
    raise SystemExit(no_data(listed.returncode, listed.stdout + listed.stderr))

gpus = [gpu_entities(PREFIX, AVAIL_T, g, STATS, DEVICE, len(first) > 1) for g in first]
gpu_by_idx = {g["idx"]: g for g in gpus}
expected = len(gpus)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        for g in gpus:
            for config, disc in gpu_discovery(g):
                client.publish(config, json.dumps(disc))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

proc = subprocess.Popen(QUERY + ["--loop=1"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True, bufsize=1)

def cleanup(*_, code=0):
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    os._exit(code)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

t_prev = time.monotonic()

def publish(sample):
    global t_prev
    t_now = time.monotonic()
    dt = max(t_now - t_prev, 0.001)
    t_prev = t_now
    for s in sample:
        g = gpu_by_idx.get(s["idx"])
        if not g:
            continue
        g["energy_wh"] += s["power"] * (dt / 3600.0)
        client.publish(g["state_p"], f"{s['power']:.1f}")
        client.publish(g["state_e"], f"{g['energy_wh'] / 1000.0:.6f}")
        for i, state, _, _ in g["stats"]:
            if not s["extra"][i].startswith("["):
                client.publish(state, stat_value(STATS, s["extra"], i))
        publish_gpu_extras(client, g, s["extra"], STATS)

# A stream that ends (driver reload, nvidia-smi killed) or a bad sample exits non-zero so systemd restarts it.
try:
    publish(first)
    while (sample := read_n(proc, expected)) is not None:
        publish(sample)
    print(f"nvidia-smi --loop ended (rc={proc.wait()})", flush=True)
except Exception:
    traceback.print_exc()
cleanup(code=1)
