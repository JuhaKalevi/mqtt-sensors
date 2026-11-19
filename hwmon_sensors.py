#!/usr/bin/env python3
"""hwmon temperatures, fans and voltages → MQTT + Home Assistant discovery (held sysfs fds, 5 s)."""
import json, os, re, signal, time
from pathlib import Path
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client,
    handle_cli, slot_id
)
from lib.board_common import dmi_board_id

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device()
BOARD = dmi_board_id()
CLIENT_ID = f"hwmon-sensors-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/{slot_id('hwmon_sensors')}/availability"
SKIP = {"drivetemp", "nvme", "nouveau"}
MAIN_CHIPS = ("k10temp", "coretemp", "zenpower", "amdgpu", "radeon", "i915", "xe")
KINDS = {
    "temp": ("Temperature", "°C", "temperature", 1000.0, 1),
    "fan": ("Fan Speed", "RPM", None, 1, 0),
    "in": ("Voltage", "V", "voltage", 1000.0, 3),
}

def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

def inputs():
    """(chip, input, label, path, kind) of every hwmon input, from directory listings and names only."""
    chips = []
    for d in sorted(Path("/sys/class/hwmon").iterdir()):
        name = (d / "name").read_text().strip()
        if name not in SKIP:
            chips.append((name, d))
    counts = {}
    for name, _ in chips:
        counts[name] = counts.get(name, 0) + 1
    for name, d in chips:
        chip = name if counts[name] == 1 else f"{name}_{slug(os.path.basename(os.path.realpath(d / 'device')))}"
        for f in sorted(d.glob("*_input")):
            m = re.fullmatch(r"(temp|fan|in)(\d+)_input", f.name)
            if not m:
                continue
            inp = f"{m.group(1)}{m.group(2)}"
            lf = d / f"{inp}_label"
            yield chip, inp, lf.read_text().strip() if lf.exists() else inp, f, m.group(1)

def main_chip(chip):
    """CPU and GPU chips: only their temperatures are on the device page, everything else under Diagnostic."""
    return any(chip == n or chip.startswith(f"{n}_") for n in MAIN_CHIPS)

def name_of(chip, inp):
    return f"hwmon_{slug(chip)}_{inp}"

def unique_of(chip, inp):
    """Motherboard-keyed: chip-and-channel names collide across machines, so the board id goes in front."""
    return f"mb_{BOARD}_{name_of(chip, inp)}"

handle_cli(
    CLIENT_ID, settings, owned=(slot_id("hwmon_sensors"),),
    entities=lambda: tuple(f"sensor/{unique_of(c, i)}" for c, i, *_ in inputs()),
)

EXCLUDE = {x.strip() for x in os.environ["HWMON_SENSORS_EXCLUDE"].split(",") if x.strip()}
sensors = []
for chip, inp, label, f, k in inputs():
    if f"{chip}/{label}" in EXCLUDE:
        continue
    fd = os.open(f, os.O_RDONLY)
    try:
        int(os.pread(fd, 32, 0))
    except OSError:
        os.close(fd)
        continue
    kind, unit, dclass, scale, prec = KINDS[k]
    unique = unique_of(chip, inp)
    state = f"{PREFIX}/sensor/{unique}/state"
    sensors.append({
        "fd": fd, "scale": scale, "state": state,
        "config": f"{PREFIX}/sensor/{unique}/config",
        "disc": make_sensor_discovery(f"{chip} {label} {kind}", state, AVAIL_T, unique, DEVICE,
                                      unit, dclass, "measurement") | {"suggested_display_precision": prec}
                | ({} if k == "temp" and main_chip(chip) else {"entity_category": "diagnostic"}),
    })
if not sensors:
    raise SystemExit("no hwmon sensors")

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        for s in sensors:
            client.publish(s["config"], json.dumps(s["disc"]))
        client.publish(AVAIL_T, "online")

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

def cleanup(*_):
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    os._exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

while True:
    for s in sensors:
        v = int(os.pread(s["fd"], 32, 0))
        client.publish(s["state"], str(v if s["scale"] == 1 else v / s["scale"]))
    time.sleep(5.0)
