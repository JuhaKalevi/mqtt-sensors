#!/usr/bin/env python3
"""LightDM unit active → MQTT + Home Assistant switch (cgroup; systemctl start/stop only in control mode)."""
import json, os, subprocess, sys, time
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"lightdm-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
MODE = os.getenv("LIGHTDM_SWITCH_MODE") or "read_only"
if MODE not in ("read_only", "control"):
    sys.exit(f"lightdm_active: LIGHTDM_SWITCH_MODE must be read_only or control, got {MODE!r}")

OBJ = f"lightdm_active_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/binary_sensor/lightdm_{HOSTNAME}/availability"
STATE = f"{PREFIX}/switch/{OBJ}/state"
CMD = f"{PREFIX}/switch/{OBJ}/set"
CONFIG = f"{PREFIX}/switch/{OBJ}/config"
OLD_STATE = f"{PREFIX}/binary_sensor/{OBJ}/state"
OLD_CONFIG = f"{PREFIX}/binary_sensor/{OBJ}/config"

DISCOVERY = {
    "name": "LightDM Active",
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

CGROUP = "/sys/fs/cgroup/system.slice/lightdm.service"

def state():
    return "ON" if os.path.isdir(CGROUP) else "OFF"

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(OLD_CONFIG, "", retain=True)
        client.publish(OLD_STATE, "", retain=True)
        client.publish(CONFIG, json.dumps(DISCOVERY), retain=True)
        client.subscribe(CMD)
        client.publish(AVAIL_T, "online", retain=True)

def on_message(client, userdata, msg):
    payload = msg.payload.decode()
    if MODE == "control" and payload in ("ON", "OFF"):
        action = "start" if payload == "ON" else "stop"
        rc = subprocess.run(["systemctl", action, "lightdm.service"]).returncode
        print(f"lightdm_active: {payload} -> systemctl {action} lightdm.service rc={rc}", flush=True)
    else:
        print(f"lightdm_active: ignored {payload!r} (mode={MODE})", flush=True)
    client.publish(STATE, state(), retain=True)

print(f"lightdm_active: host={HOSTNAME} mode={MODE}", flush=True)

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
