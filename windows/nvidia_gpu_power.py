#!/usr/bin/env python3
"""Windows NVIDIA GPU power + energy → MQTT + HA (nvidia-smi --loop).

Linux reference: nvidia_gpu_power.py. Same object ids. No select(); N lines per sample.
"""
import functools, json, os, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device,
    create_client, handle_cli, slot_id
)
from lib.nvidia_common import clean_gpu_name, gpu_entities, gpu_lists

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device("windows")
CLIENT_ID = f"gpu-power-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/{slot_id('gpu_power')}/availability"

def hidden_console():
    info = subprocess.STARTUPINFO()
    info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    info.wShowWindow = 0
    return info

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

@functools.cache
def lists():
    """Only for the CLI flags: one nvidia-smi query for each GPU's index, UUID and name."""
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,uuid,name", "--format=csv,noheader"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
        startupinfo=hidden_console(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    ).stdout
    gpus = [tuple(p.strip() for p in line.split(",")) for line in out.splitlines() if line.count(",") == 2]
    return gpu_lists(gpus, [s[1] for s in ALL_STATS])

handle_cli(CLIENT_ID, settings, entities=lists, owned=(slot_id("gpu_power"),))
STATS = ALL_STATS if os.environ["NVIDIA_GPU_STATS"] else []
FIELDS = ",".join(["index", "name", "power.draw"] + [s[0] for s in STATS] + (["memory.total"] if STATS else []) + ["uuid"])
QUERY = ["nvidia-smi", f"--query-gpu={FIELDS}", "--format=csv,noheader,nounits"]

def stat_value(extra, i):
    if STATS[i][0] == "memory.used":
        return str(float(extra[i]) / float(extra[-1]) * 100)
    return extra[i]

def parse_line(line):
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        return None
    try:
        idx = int(parts[0])
    except ValueError:
        return None
    try:
        power = float(parts[2])
    except ValueError:
        power = 0.0
    return {"idx": idx, "name": clean_gpu_name(parts[1]), "power": power, "uuid": parts[-1], "extra": parts[3:-1]}

def read_n(proc, n):
    rows = []
    while len(rows) < n:
        line = proc.stdout.readline()
        if not line:
            return None
        row = parse_line(line.strip())
        if row:
            rows.append(row)
    return rows

listed = subprocess.run(
    QUERY, capture_output=True, text=True, stdin=subprocess.DEVNULL,
    startupinfo=hidden_console(),
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
)
rows = [parse_line(line.strip()) for line in (listed.stdout or "").splitlines()]
rows = [row for row in rows if row]
if not rows:
    raise SystemExit("nvidia-smi produced no data (no GPUs or driver problem)")

gpus = []
for g in rows:
    gpus.append(gpu_entities(PREFIX, AVAIL_T, g, STATS, DEVICE, len(rows) > 1))
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

proc = subprocess.Popen(
    QUERY + ["--loop=1"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    bufsize=1,
    startupinfo=hidden_console(),
    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
)
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
                client.publish(state, stat_value(s["extra"], i))

def stop():
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    if proc.poll() is None:
        proc.terminate()

try:
    publish(rows)
    while True:
        sample = read_n(proc, expected)
        if sample is None:
            raise SystemExit("nvidia-smi stream ended")
        publish(sample)
except KeyboardInterrupt:
    pass
finally:
    stop()
