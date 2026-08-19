#!/usr/bin/python3 -Bu
"""NVIDIA GPU power → MQTT + Home Assistant discovery (nvidia-smi --loop)."""
import time, re, subprocess, json, signal, os
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, make_power_discovery, create_client
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
    """Launch a single long-running nvidia-smi that emits one sample set per second."""
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
        bufsize=1,          # line-buffered
    )

def read_sample(proc, expected_count=None):
    """
    Read one complete sample (one line per GPU).
    Returns list of dicts or None if the process died / EOF.
    """
    lines = []
    while True:
        line = proc.stdout.readline()
        if not line:                        # EOF → process exited
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
        # When we don't yet know the count, stop after a short quiet period
        # (first sample only). Subsequent calls always use expected_count.
        if expected_count is None and len(lines) > 0:
            # peek whether more data is immediately available
            # (simple heuristic; works because nvidia-smi writes the whole set at once)
            import select
            if not select.select([proc.stdout], [], [], 0.05)[0]:
                break
    return lines

# ---------- discovery (one-shot first sample) ----------
proc = start_smi_stream()
first = read_sample(proc)
if not first:
    proc.terminate()
    raise SystemExit("nvidia-smi produced no data (no GPUs or driver problem)")

multi = len(first) > 1
gpus = []
for g in first:
    unique = f"gpu{g['idx']}_power_{HOSTNAME}"
    state_t = f"{PREFIX}/sensor/{unique}/state"
    config_t = f"{PREFIX}/sensor/{unique}/config"
    entity = f"{g['name']} (GPU {g['idx']}) Power" if multi else f"{g['name']} Power"
    disc = make_power_discovery(entity, state_t, AVAIL_T, unique, DEVICE)
    gpus.append({
        "idx": g["idx"],
        "unique": unique,
        "state_t": state_t,
        "config_t": config_t,
        "discovery": disc,
    })

# Keep a stable mapping by index for the rest of the run
gpu_by_idx = {g["idx"]: g for g in gpus}
expected = len(gpus)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        for g in gpus:
            client.publish(g["config_t"], json.dumps(g["discovery"]), retain=True)
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

try:
    # First sample already consumed for discovery; publish its power values
    for sample in first:
        g = gpu_by_idx.get(sample["idx"])
        if g:
            client.publish(g["state_t"], f"{sample['power']:.1f}", retain=True)

    # Continuous stream
    while True:
        sample = read_sample(proc, expected_count=expected)
        if sample is None:
            break
        for s in sample:
            g = gpu_by_idx.get(s["idx"])
            if g:
                client.publish(g["state_t"], f"{s['power']:.1f}", retain=True)
except Exception:
    pass
finally:
    cleanup()
