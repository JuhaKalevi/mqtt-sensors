#!/usr/bin/env python3
"""Windows NVIDIA GPU power + energy → MQTT + HA (nvidia-smi --loop).

Linux reference: nvidia_gpu_power.py. Same object ids. No select(); N lines per sample.
"""
import json, os, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])
from mqtt_common import (
    get_hostname, get_mqtt_settings,
    make_power_discovery, make_energy_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE = {"identifiers": [f"windows_host_{HOSTNAME}"], "name": HOSTNAME}
CLIENT_ID = f"gpu-power-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/gpu_power_{HOSTNAME}/availability"
QUERY = ["nvidia-smi", "--query-gpu=index,name,power.draw", "--format=csv,noheader,nounits"]

def clean_gpu_name(name):
    name = re.sub(r"^NVIDIA\s+", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "GPU"

def parse_line(line):
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 3:
        return None
    try:
        idx = int(parts[0])
    except ValueError:
        return None
    try:
        power = float(parts[2])
    except ValueError:
        power = 0.0
    return {"idx": idx, "name": clean_gpu_name(parts[1]), "power": power}

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

listed = subprocess.run(QUERY, capture_output=True, text=True, stdin=subprocess.DEVNULL)
rows = [parse_line(line.strip()) for line in (listed.stdout or "").splitlines()]
rows = [row for row in rows if row]
if not rows:
    raise SystemExit("nvidia-smi produced no data (no GPUs or driver problem)")

multi = len(rows) > 1
gpus = []
for g in rows:
    unique = f"gpu{g['idx']}_power_{HOSTNAME}"
    unique_e = f"gpu{g['idx']}_energy_{HOSTNAME}"
    state_p = f"{PREFIX}/sensor/{unique}/state"
    state_e = f"{PREFIX}/sensor/{unique_e}/state"
    config_p = f"{PREFIX}/sensor/{unique}/config"
    config_e = f"{PREFIX}/sensor/{unique_e}/config"
    base = f"{g['name']} (GPU {g['idx']})" if multi else g["name"]
    gpus.append({
        "idx": g["idx"],
        "state_p": state_p,
        "state_e": state_e,
        "config_p": config_p,
        "config_e": config_e,
        "disc_p": make_power_discovery(f"{base} Power", state_p, AVAIL_T, unique, DEVICE),
        "disc_e": make_energy_discovery(f"{base} Energy", state_e, AVAIL_T, unique_e, DEVICE),
        "energy_wh": 0.0,
    })
gpu_by_idx = {g["idx"]: g for g in gpus}
expected = len(gpus)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        for g in gpus:
            client.publish(g["config_p"], json.dumps(g["disc_p"]), retain=True)
            client.publish(g["config_e"], json.dumps(g["disc_e"]), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

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
        client.publish(g["state_p"], f"{s['power']:.1f}", retain=True)
        client.publish(g["state_e"], f"{g['energy_wh'] / 1000.0:.6f}", retain=True)

def stop():
    client.publish(AVAIL_T, "offline", retain=True)
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
