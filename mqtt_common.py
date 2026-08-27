#!/usr/bin/env python3
"""Minimal shared MQTT + Home Assistant discovery helpers."""
import os, json, socket, pwd
from pathlib import Path
import paho.mqtt.client as mqtt

def load_dotenv(path=".env"):
    if not os.path.isfile(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

def get_hostname():
    return socket.gethostname().split(".")[0]

def chia_root():
    return Path(pwd.getpwuid(1000).pw_dir) / ".chia" / "mainnet"

def get_mqtt_settings():
    load_dotenv()
    return {
        "host": os.getenv("MQTT_HOST", "localhost"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
        "user": os.getenv("MQTT_USER") or None,
        "password": os.getenv("MQTT_PASS") or None,
        "prefix": os.getenv("MQTT_PREFIX", "homeassistant"),
    }

def make_device(hostname):
    device_id = f"linux_host_{hostname}"
    return {"identifiers": [device_id], "name": hostname}, device_id

def make_sensor_discovery(name, state_topic, availability_topic, unique_id, device,
                          unit, device_class, state_class):
    return {
        "name": name,
        "state_topic": state_topic,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "unit_of_measurement": unit,
        "device_class": device_class,
        "state_class": state_class,
        "unique_id": unique_id,
        "device": device,
    }

def make_power_discovery(name, state_topic, availability_topic, unique_id, device):
    return make_sensor_discovery(
        name, state_topic, availability_topic, unique_id, device,
        unit="W", device_class="power", state_class="measurement"
    )

def make_energy_discovery(name, state_topic, availability_topic, unique_id, device):
    return make_sensor_discovery(
        name, state_topic, availability_topic, unique_id, device,
        unit="kWh", device_class="energy", state_class="total_increasing"
    )

def create_client(client_id, settings, will_topic=None, will_payload="offline"):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if settings["user"]:
        client.username_pw_set(settings["user"], settings["password"])
    if will_topic:
        client.will_set(will_topic, will_payload, qos=1, retain=True)
    return client
