#!/usr/bin/env python3
"""systemd unit active → MQTT + Home Assistant switch (cgroup; systemctl start/stop only in control mode)."""
import json, os, re, subprocess, sys, time
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
UNIT = re.sub(r"\.service$", "", os.getenv("SWITCH_UNIT") or "")
if not UNIT:
    sys.exit("systemd_unit_switch: SWITCH_UNIT is not set (use systemd_unit_switch@<unit>.service)")
KEY = re.sub(r"[^A-Z0-9]", "_", UNIT.upper())
UNIT_ID = KEY.lower()
MODE = os.getenv(f"SWITCH_MODE_{KEY}") or "read_only"
if MODE not in ("read_only", "control"):
    sys.exit(f"systemd_unit_switch: SWITCH_MODE_{KEY} must be read_only or control, got {MODE!r}")
NAME = os.getenv(f"SWITCH_NAME_{KEY}") or UNIT.replace("-", " ").replace("_", " ").title()
CLIENT_ID = f"{UNIT_ID}-{HOSTNAME}"

OBJ = f"{UNIT_ID}_active_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/{UNIT_ID}_{HOSTNAME}/availability"
STATE = f"{PREFIX}/switch/{OBJ}/state"
CMD = f"{PREFIX}/switch/{OBJ}/set"
CONFIG = f"{PREFIX}/switch/{OBJ}/config"

DISCOVERY = {
    "name": f"{NAME} Active",
    "state_topic": STATE,
    "command_topic": CMD,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "payload_on": "ON",
    "payload_off": "OFF",
    "unique_id": OBJ,
    "device": DEVICE,
}

CGROUP = f"/sys/fs/cgroup/system.slice/{UNIT}.service"

def state():
    return "ON" if os.path.isdir(CGROUP) else "OFF"

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG, json.dumps(DISCOVERY), retain=True)
        client.subscribe(CMD)
        client.publish(AVAIL_T, "online", retain=True)

def on_message(client, userdata, msg):
    payload = msg.payload.decode()
    if MODE == "control" and payload in ("ON", "OFF"):
        action = "start" if payload == "ON" else "stop"
        rc = subprocess.run(["systemctl", action, f"{UNIT}.service"]).returncode
        print(f"systemd_unit_switch: {payload} -> systemctl {action} {UNIT}.service rc={rc}", flush=True)
    else:
        print(f"systemd_unit_switch: {UNIT}: ignored {payload!r} (mode={MODE})", flush=True)
    client.publish(STATE, state(), retain=True)

print(f"systemd_unit_switch: host={HOSTNAME} unit={UNIT}.service name={NAME!r} mode={MODE}", flush=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.on_message = on_message
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        client.publish(STATE, state(), retain=True)
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
