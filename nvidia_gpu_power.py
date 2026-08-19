#!/usr/bin/env python3
"""NVIDIA GPU power + energy → MQTT + Home Assistant discovery (nvidia-smi --loop)."""
import time, re, subprocess, json, signal, os, select
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_power_discovery, make_energy_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"gpu-power-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/gpu_power_{HOSTNAME}/availability"

def clean_gpu_name(name):
    name = re.sub(r"^NVIDIA\s+", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "GPU"

def start_smi_stream():
    return subprocess.Popen(
        [
            "nvidia-smi",
            "--query-gpu=index,name,power.draw",
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
        if len(parts) < 3:
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
        lines.append({"idx": idx, "name": name, "power": power})
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

multi = len(first) > 1
gpus = []
for g in first:
    unique = f"gpu{g['idx']}_power_{HOSTNAME}"
    unique_e = f"gpu{g['idx']}_energy_{HOSTNAME}"
    state_p = f"{PREFIX}/sensor/{unique}/state"
    state_e = f"{PREFIX}/sensor/{unique_e}/state"
    config_p = f"{PREFIX}/sensor/{unique}/config"
    config_e = f"{PREFIX}/sensor/{unique_e}/config"
    base = f"{g['name']} (GPU {g['idx']})" if multi else g["name"]
    disc_p = make_power_discovery(f"{base} Power", state_p, AVAIL_T, unique, DEVICE)
    disc_e = make_energy_discovery(f"{base} Energy", state_e, AVAIL_T, unique_e, DEVICE)
    gpus.append({
        "idx": g["idx"],
        "state_p": state_p,
        "state_e": state_e,
        "config_p": config_p,
        "config_e": config_e,
        "disc_p": disc_p,
        "disc_e": disc_e,
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

def cleanup(*_):
    client.publish(AVAIL_T, "offline", retain=True)
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
        client.publish(g["state_p"], f"{power:.1f}", retain=True)
        client.publish(g["state_e"], f"{g['energy_wh'] / 1000.0:.6f}", retain=True)

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
            client.publish(g["state_p"], f"{power:.1f}", retain=True)
            client.publish(g["state_e"], f"{g['energy_wh'] / 1000.0:.6f}", retain=True)
except Exception:
    pass
finally:
    cleanup()
