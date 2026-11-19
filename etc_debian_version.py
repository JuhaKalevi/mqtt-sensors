#!/usr/bin/env python3
"""/etc/debian_version → MQTT + Home Assistant discovery, published when its contents change (inotify on /etc)."""
import ctypes, json, os, struct
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, create_client, handle_cli, slot_id
)

MACHINE_ID = get_machine_id()
DEVICE, _ = make_device()
CLIENT_ID = f"etc-debian-version-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
OBJ = slot_id("etc_debian_version")
handle_cli(CLIENT_ID, settings, entities=(f"sensor/{OBJ}",), owned=(OBJ,))

AVAIL_T = f"{PREFIX}/sensor/{OBJ}/availability"
STATE = f"{PREFIX}/sensor/{OBJ}/state"
CONFIG = f"{PREFIX}/sensor/{OBJ}/config"
DISCOVERY = make_sensor_discovery("Debian Version", STATE, AVAIL_T, OBJ, DEVICE)

DIR, NAME = b"/etc", b"debian_version"
IN_CLOSE_WRITE, IN_MOVED_TO, IN_Q_OVERFLOW, IN_CLOEXEC = 0x8, 0x80, 0x4000, 0o2000000
EVENT = struct.Struct("iIII")

libc = ctypes.CDLL(None, use_errno=True)
fd = libc.inotify_init1(IN_CLOEXEC)
if fd < 0 or libc.inotify_add_watch(fd, DIR, IN_CLOSE_WRITE | IN_MOVED_TO) < 0:
    raise OSError(ctypes.get_errno(), "inotify on /etc")

def read_version():
    try:
        with open(DIR + b"/" + NAME, "rb") as f:
            return f.read().decode(errors="replace").strip()
    except FileNotFoundError:
        return None

def touched(buf):
    i = 0
    while i < len(buf):
        _, mask, _, size = EVENT.unpack_from(buf, i)
        name = buf[i + EVENT.size:i + EVENT.size + size].rstrip(b"\0")
        i += EVENT.size + size
        if name == NAME or mask & IN_Q_OVERFLOW:
            return True
    return False

version = read_version()

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(CONFIG, json.dumps(DISCOVERY))
        client.publish(AVAIL_T, "online")
        if version is not None:
            client.publish(STATE, version)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T, replay_states=True)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        if touched(os.read(fd, 65536)):
            current = read_version()
            if current is not None and current != version:
                version = current
                client.publish(STATE, version)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
