#!/usr/bin/env python3
"""Chia farm size → MQTT + Home Assistant discovery (farmer + full node RPC)."""
import time, json, ssl, http.client, pwd
from pathlib import Path
from mqtt_common import (
    get_hostname, get_mqtt_settings, make_device,
    make_sensor_discovery, create_client
)

HOSTNAME = get_hostname()
DEVICE, DEVICE_ID = make_device(HOSTNAME)
CLIENT_ID = f"chia-farm-{HOSTNAME}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]

OBJ_PLOTS = f"chia_plots_{HOSTNAME}"
OBJ_SIZE = f"chia_farm_size_{HOSTNAME}"
OBJ_EFF = f"chia_farm_effective_{HOSTNAME}"
OBJ_NET = f"chia_netspace_{HOSTNAME}"
OBJ_ETA = f"chia_eta_{HOSTNAME}"
AVAIL_T = f"{PREFIX}/sensor/chia_farm_{HOSTNAME}/availability"

STATE_PLOTS = f"{PREFIX}/sensor/{OBJ_PLOTS}/state"
STATE_SIZE = f"{PREFIX}/sensor/{OBJ_SIZE}/state"
STATE_EFF = f"{PREFIX}/sensor/{OBJ_EFF}/state"
STATE_NET = f"{PREFIX}/sensor/{OBJ_NET}/state"
STATE_ETA = f"{PREFIX}/sensor/{OBJ_ETA}/state"
CONFIG_PLOTS = f"{PREFIX}/sensor/{OBJ_PLOTS}/config"
CONFIG_SIZE = f"{PREFIX}/sensor/{OBJ_SIZE}/config"
CONFIG_EFF = f"{PREFIX}/sensor/{OBJ_EFF}/config"
CONFIG_NET = f"{PREFIX}/sensor/{OBJ_NET}/config"
CONFIG_ETA = f"{PREFIX}/sensor/{OBJ_ETA}/config"

DISCOVERY_PLOTS = {
    "name": "Chia Plots",
    "state_topic": STATE_PLOTS,
    "availability_topic": AVAIL_T,
    "payload_available": "online",
    "payload_not_available": "offline",
    "state_class": "measurement",
    "unique_id": OBJ_PLOTS,
    "device": DEVICE,
}
DISCOVERY_SIZE = make_sensor_discovery(
    "Chia Farm Size", STATE_SIZE, AVAIL_T, OBJ_SIZE, DEVICE,
    unit="TiB", device_class="data_size", state_class="measurement"
)
DISCOVERY_EFF = make_sensor_discovery(
    "Chia Farm Effective Size", STATE_EFF, AVAIL_T, OBJ_EFF, DEVICE,
    unit="TiB", device_class="data_size", state_class="measurement"
)
DISCOVERY_NET = make_sensor_discovery(
    "Chia Estimated Netspace", STATE_NET, AVAIL_T, OBJ_NET, DEVICE,
    unit="EiB", device_class="data_size", state_class="measurement"
)
DISCOVERY_ETA = make_sensor_discovery(
    "Chia ETA to Win", STATE_ETA, AVAIL_T, OBJ_ETA, DEVICE,
    unit="s", device_class="duration", state_class="measurement"
)

CHIA_ROOT = Path(pwd.getpwuid(1000).pw_dir) / ".chia" / "mainnet"
TIB = 1024 ** 4
EIB = 1024 ** 6
BLOCK_S = 18.75

def chia_conn(kind, port):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    base = CHIA_ROOT / "config" / "ssl" / kind
    ctx.load_cert_chain(str(base / f"private_{kind}.crt"), str(base / f"private_{kind}.key"))
    c = http.client.HTTPSConnection("localhost", port, context=ctx, timeout=15)
    c.connect()
    return c

def rpc(c, path):
    c.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
    return json.loads(c.getresponse().read())

farmer = chia_conn("farmer", 8559)
node = chia_conn("full_node", 8555)

def farm_stats():
    data = rpc(farmer, "/get_harvesters_summary")
    plots = size = effective = 0
    for h in data["harvesters"]:
        plots += int(h.get("plots") or 0)
        size += int(h.get("total_plot_size") or 0)
        effective += int(h.get("total_effective_plot_size") or 0)
    space = int(rpc(node, "/get_blockchain_state")["blockchain_state"]["space"])
    eta = (space / effective) * BLOCK_S if effective else 0
    return plots, size / TIB, effective / TIB, space / EIB, eta

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.publish(CONFIG_PLOTS, json.dumps(DISCOVERY_PLOTS), retain=True)
        client.publish(CONFIG_SIZE, json.dumps(DISCOVERY_SIZE), retain=True)
        client.publish(CONFIG_EFF, json.dumps(DISCOVERY_EFF), retain=True)
        client.publish(CONFIG_NET, json.dumps(DISCOVERY_NET), retain=True)
        client.publish(CONFIG_ETA, json.dumps(DISCOVERY_ETA), retain=True)
        client.publish(AVAIL_T, "online", retain=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        plots, size, effective, netspace, eta = farm_stats()
        client.publish(STATE_PLOTS, str(plots), retain=True)
        client.publish(STATE_SIZE, f"{size:.3f}", retain=True)
        client.publish(STATE_EFF, f"{effective:.3f}", retain=True)
        client.publish(STATE_NET, f"{netspace:.3f}", retain=True)
        client.publish(STATE_ETA, f"{eta:.0f}", retain=True)
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline", retain=True)
    client.loop_stop()
    client.disconnect()
    farmer.close()
    node.close()
