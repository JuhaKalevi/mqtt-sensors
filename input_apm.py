#!/usr/bin/env python3
"""Mouse, keyboard and total actions per minute → MQTT + Home Assistant discovery (evdev, 60 s window, 5 s)."""
import json, os, select, signal, struct, time
from collections import deque
from pathlib import Path
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, _ = make_device(HOSTNAME)
CLIENT_ID = f"input-apm-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
AVAIL_T = f"{PREFIX}/sensor/input_apm_{HOSTNAME}/availability"
EVENT = struct.Struct("llHHi")
EV_KEY, KEY_A, BTN_LEFT, BTN_TASK = 1, 30, 0x110, 0x117

def kind(code):
    if code < 0x100:
        return "keyboard"
    if BTN_LEFT <= code <= BTN_TASK:
        return "mouse"
    return None

def has_key(event, bit):
    words = (Path("/sys/class/input") / event / "device/capabilities/key").read_text().split()
    return bool(int("".join(w.zfill(16) for w in words), 16) >> bit & 1)

SENSORS = {}
for k, title in (("mouse", "Mouse APM"), ("keyboard", "Keyboard APM"), ("total", "Total APM")):
    unique = f"input_apm_{k}_{HOSTNAME}"
    state = f"{PREFIX}/sensor/{unique}/state"
    SENSORS[k] = (state, f"{PREFIX}/sensor/{unique}/config",
                  make_sensor_discovery(title, state, AVAIL_T, unique, DEVICE, "APM", None, "measurement"))

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

def cleanup(*_):
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    os._exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

fds = {}
def rescan():
    for p in Path("/dev/input").glob("event*"):
        if p.name not in fds.values() and (has_key(p.name, BTN_LEFT) or has_key(p.name, KEY_A)):
            fds[os.open(p, os.O_RDONLY | os.O_NONBLOCK)] = p.name

presses = {"mouse": deque(), "keyboard": deque()}
rescan()
if not fds:
    raise SystemExit("no mouse or keyboard event devices")
next_publish = time.monotonic() + 5.0
while True:
    ready, _, _ = select.select(list(fds), [], [], max(next_publish - time.monotonic(), 0))
    now = time.monotonic()
    for fd in ready:
        try:
            data = os.read(fd, EVENT.size * 64)
        except OSError:
            os.close(fd)
            del fds[fd]
            continue
        for i in range(0, len(data), EVENT.size):
            _, _, etype, code, value = EVENT.unpack_from(data, i)
            k = kind(code) if etype == EV_KEY and value == 1 else None
            if k:
                presses[k].append(now)
    if now >= next_publish:
        for q in presses.values():
            while q and q[0] < now - 60.0:
                q.popleft()
        client.publish(SENSORS["mouse"][0], str(len(presses["mouse"])))
        client.publish(SENSORS["keyboard"][0], str(len(presses["keyboard"])))
        client.publish(SENSORS["total"][0], str(len(presses["mouse"]) + len(presses["keyboard"])))
        rescan()
        next_publish = now + 5.0
