#!/usr/bin/env python3
"""Minimal shared MQTT + Home Assistant discovery helpers."""
import os, json, socket, subprocess
from contextlib import contextmanager
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
    import pwd
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

def make_switch_discovery(name, state_topic, command_topic, availability_topic, unique_id, device):
    return {
        "name": name,
        "state_topic": state_topic,
        "command_topic": command_topic,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "payload_on": "ON",
        "payload_off": "OFF",
        "unique_id": unique_id,
        "device": device,
    }

def switch_mode(env_name):
    mode = os.getenv(env_name) or "read_only"
    if mode not in ("read_only", "control"):
        raise SystemExit(f"{env_name} must be read_only or control, got {mode!r}")
    return mode

def make_switch_on_message(tag, mode, state_topic, get_state, act):
    def on_message(client, userdata, msg):
        payload = msg.payload.decode()
        if mode == "control" and payload in ("ON", "OFF"):
            act(payload)
        else:
            print(f"{tag}: ignored {payload!r} (mode={mode})", flush=True)
        client.publish(state_topic, get_state(), retain=True)
    return on_message

class SystemdServiceManager:
    """Service manager backend. Contract: is_active(name) -> bool, start(name), stop(name), follow_log(name) -> context manager yielding an iterator of new log lines (text, newline kept; stopped on exit); name is the bare service name ("lightdm", "ollama", "chia_recompute_server").
    Collectors only use service_manager() and these four methods, never systemd directly. systemd is the only shipped backend.
    Another init system (OpenRC, runit, s6, ...): add a class with the same four methods and return it from service_manager(); no collector changes."""

    def is_active(self, name):
        return os.path.isdir(f"/sys/fs/cgroup/system.slice/{name}.service")

    def start(self, name):
        self._systemctl("start", name)

    def stop(self, name):
        self._systemctl("stop", name)

    def _systemctl(self, action, name):
        rc = subprocess.run(["systemctl", action, f"{name}.service"]).returncode
        print(f"{'ON' if action == 'start' else 'OFF'} -> systemctl {action} {name}.service rc={rc}", flush=True)

    @contextmanager
    def follow_log(self, name):
        proc = subprocess.Popen(
            ["journalctl", "-u", name, "-f", "-n", "0", "-o", "cat", "--no-pager"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        try:
            yield proc.stdout
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait()

def service_manager():
    return SystemdServiceManager()

class LogindSessionSource:
    """Login session backend. Contract: active_users() -> list of user names with an active or online session (unsorted).
    Collectors only use session_source() and this method, never logind directly. logind's /run/systemd/users is the only shipped backend; elogind writes the same files, so non-systemd hosts with elogind already work.
    Another session tracker (ConsoleKit2, seatd, utmp, ...): add a class with the same method and return it from session_source(); no collector changes."""

    USERS = "/run/systemd/users"

    def active_users(self):
        names = []
        for fn in os.listdir(self.USERS):
            if not fn.isdigit():
                continue
            user = state = None
            try:
                with open(os.path.join(self.USERS, fn)) as f:
                    for line in f:
                        if line.startswith("NAME="):
                            user = line[5:].strip()
                        elif line.startswith("STATE="):
                            state = line[6:].strip()
            except FileNotFoundError:
                continue
            if user and state in ("active", "online"):
                names.append(user)
        return names

def session_source():
    return LogindSessionSource()

def create_client(client_id, settings, will_topic=None, will_payload="offline"):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if settings["user"]:
        client.username_pw_set(settings["user"], settings["password"])
    if will_topic:
        client.will_set(will_topic, will_payload, qos=1, retain=True)
    return client
