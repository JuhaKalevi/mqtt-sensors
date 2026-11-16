#!/usr/bin/env python3
"""Minimal shared MQTT + Home Assistant discovery helpers."""
import argparse, os, json, socket, subprocess, sys, threading, time
from contextlib import contextmanager
from pathlib import Path
import paho.mqtt.client as mqtt

# Non-retained availability republish while a collector is connected.
# After a purge, HA entities recover within this interval without a restart.
AVAILABILITY_HEARTBEAT_SEC = 60
DISCOVERY_SETTLE_SEC = 2
SWEEP_SUBACK_TIMEOUT_SEC = 10
SWEEP_MODES = ("off", "dry", "apply")


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
        "sweep": os.getenv("MQTT_SWEEP") or "dry",
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

def make_number_discovery(name, state_topic, command_topic, availability_topic, unique_id, device,
                          min_value=0, max_value=100, step=1, mode="slider", unit=None):
    d = {
        "name": name,
        "state_topic": state_topic,
        "command_topic": command_topic,
        "availability_topic": availability_topic,
        "payload_available": "online",
        "payload_not_available": "offline",
        "min": min_value,
        "max": max_value,
        "step": step,
        "mode": mode,
        "unique_id": unique_id,
        "device": device,
    }
    if unit is not None:
        d["unit_of_measurement"] = unit
    return d

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
        client.publish(state_topic, get_state())
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

def on_ha_status(client, userdata, msg):
    if msg.payload == b"online" and not msg.retain and client.on_connect:
        client.on_connect(client, userdata, None, 0, None)

def _payload_bytes(payload):
    if payload is None:
        return b""
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, bytearray):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode()
    return str(payload).encode()

def _retain(args, kwargs):
    return kwargs.get("retain", args[1] if len(args) > 1 else False)

def clear_retained(client, topics):
    for topic in topics:
        client.publish(topic, b"", qos=1, retain=True).wait_for_publish()  # retained: zero-length retained payload clears the stored message


def _start_availability_heartbeat(client, will_topic, will_payload, interval, replay_states=False):
    """Republish non-retained 'online' on will_topic every interval seconds.

    Also DISCOVERY_SETTLE_SEC after each 'online' the collector publishes (on
    connect and HA 'online'), so HA sees it once discovery has subscribed.
    With replay_states, the last non-retained payload of every non-config
    topic is republished with that one, for collectors that publish slowly.

    Stops when the collector publishes the will payload (usually 'offline')
    to will_topic, or when disconnect() is called, so a shutdown cannot be
    followed by a late 'online'. The thread is a daemon so a short-lived
    client cannot hang on exit.
    """
    stop = threading.Event()
    lock = threading.Lock()
    orig_publish = client.publish
    orig_disconnect = client.disconnect
    settle = threading.Event()
    last = {}
    offline = _payload_bytes(will_payload)

    def publish(topic, payload=None, *args, **kwargs):
        if topic == will_topic and _payload_bytes(payload) == offline:
            with lock:
                stop.set()
                return orig_publish(topic, payload, *args, **kwargs)
        if topic == will_topic and _payload_bytes(payload) == b"online":
            settle.set()
        elif replay_states and not topic.endswith("/config") and not _retain(args, kwargs):
            last[topic] = payload
        return orig_publish(topic, payload, *args, **kwargs)

    def disconnect(*args, **kwargs):
        with lock:
            stop.set()
        return orig_disconnect(*args, **kwargs)

    def beat():
        while True:
            settled = settle.wait(interval)
            if settled:
                settle.clear()
                if stop.wait(DISCOVERY_SETTLE_SEC):
                    return
            try:
                with lock:
                    if stop.is_set():
                        return
                    if client.is_connected():
                        orig_publish(will_topic, "online")  # not retained
                        for topic, payload in list(last.items()) if settled else ():
                            orig_publish(topic, payload)  # not retained
            except Exception:
                pass

    client.publish = publish
    client.disconnect = disconnect
    threading.Thread(target=beat, name="mqtt-avail-heartbeat", daemon=True).start()


def owns(owned, obj):
    return any(obj == p or obj.startswith(p + "_") for p in owned)

def _scan_retained(client, prefix, match):
    """Retained topics <prefix>/<component>/<object>/<leaf> for which match(component, object) is true.

    Subscribes to <prefix>/+/+/+, waits for the SUBACK, then DISCOVERY_SETTLE_SEC
    for the broker to deliver retained messages, and unsubscribes. Returns None
    when there is no SUBACK (not connected).
    """
    scope = f"{prefix}/+/+/+"
    found = set()
    subacked = threading.Event()

    def on_retained(client, userdata, msg):
        component, obj = msg.topic.split("/")[-3:-1]
        if msg.retain and msg.payload and match(component, obj):
            found.add(msg.topic)

    client.on_subscribe = lambda *a: subacked.set()
    client.message_callback_add(scope, on_retained)
    client.subscribe(scope)
    if subacked.wait(SWEEP_SUBACK_TIMEOUT_SEC):
        time.sleep(DISCOVERY_SETTLE_SEC)
    client.unsubscribe(scope)
    client.message_callback_remove(scope)
    client.on_subscribe = None
    return set(found) if subacked.is_set() else None

def force_purge(client_id, settings, entities=(), owned=(), retired=(), apply=False, match=None, keep=()):
    """List (apply=False) or force-delete (apply=True) a set of entities and their retained topics.

    entities and retired are "<component>/<object>"; owned are object ids or
    id prefixes as for the sweep; match(object) optionally selects more
    objects, each a whole entity whose config topic is included too. The
    topics are every entity and retired config topic plus every retained
    topic under an owned, entity, retired or matched object. Topics of an
    object in keep are listed as present and never cleared; with keep, the
    others are listed as stale. apply clears the
    rest with clear_retained (an empty config removes the HA entity and its
    history, retained or not). Independent of MQTT_SWEEP. Uses its own
    short-lived connection; exits when it cannot connect.
    """
    prefix = settings["prefix"]
    named = set(entities) | set(retired)
    keep = set(keep)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if settings["user"]:
        client.username_pw_set(settings["user"], settings["password"])
    connected = threading.Event()
    client.on_connect = lambda c, u, f, rc, p=None: rc == 0 and connected.set()
    client.connect(settings["host"], settings["port"], keepalive=60)
    client.loop_start()
    try:
        if not connected.wait(SWEEP_SUBACK_TIMEOUT_SEC):
            raise SystemExit("purge: not connected")
        retained = _scan_retained(client, prefix, lambda component, obj: owns(owned, obj) or f"{component}/{obj}" in named or bool(match and match(obj)))
        if retained is None:
            raise SystemExit("purge: no SUBACK")
        found = {"/".join(t.split("/")[-3:-1]) for t in retained if match and match(t.split("/")[-2])}
        topics = sorted({f"{prefix}/{e}/config" for e in named | found} | retained)
        kept = [t for t in topics if t.split("/")[-2] in keep]
        targets = [t for t in topics if t not in kept]
        for topic in topics:
            marks = (" (retained)" if topic in retained else "") + (" (present, kept)" if topic in kept else " (stale)" if keep else "")
            print(f"{'purge' if apply and topic not in kept else 'list'}: {topic}{marks}", flush=True)
        if apply:
            clear_retained(client, targets)
        print(f"{'purged' if apply else 'listed'} {len(targets)} topic(s), {len(retained)} retained, {len(kept)} kept", flush=True)
        return targets
    finally:
        client.loop_stop()
        client.disconnect()

def handle_cli(client_id, settings, entities=(), owned=(), retired=(), match=None, present=None):
    """The collector CLI shared by every script that supports it; returns when no flag is given.

    --list (read-only) and --purge (everything) go through force_purge and
    exit 0. present() -> ({object: label}, [unknown]) enables --purge-stale:
    the present objects become entities and are kept, everything else
    matched is purged; any unknown entry refuses it (exit 1). --help prints
    the usage and exits 0, an unknown flag exits 2. Runs before the
    collector creates its client or opens a source.
    """
    parser = argparse.ArgumentParser(prog=os.path.basename(sys.argv[0]), description="Without a flag, runs the collector. Flags exit without starting it.")
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument("--list", action="store_true", help="print every entity and retained topic this script owns or has retired (read-only)")
    flags.add_argument("--purge", action="store_true", help="remove all of them from HA and the broker; stop the collector first")
    if present:
        flags.add_argument("--purge-stale", action="store_true", help="remove only entities of devices that are not present; present ones are kept")
    args = parser.parse_args()
    stale = getattr(args, "purge_stale", False)
    if not (args.list or args.purge or stale):
        return
    keep, unknown = present() if present else ({}, [])
    for obj, label in sorted(keep.items()):
        print(f"present: {obj} ({label})", flush=True)
    for u in unknown:
        print(f"unknown: {u}", flush=True)
    if stale and unknown:
        raise SystemExit("purge-stale: refused, a present device's object id is unknown without waking it; remove single ghosts with mqtt_purge_retained.py --entity")
    if not args.list:
        print("purge: stop this collector first, or its next publish recreates the entities", flush=True)
    entities = tuple(entities) + tuple(f"sensor/{o}" for o in keep if not stale)
    force_purge(f"{client_id}-purge", settings, entities, owned, retired, apply=not args.list, match=match, keep=keep if not args.purge else ())
    raise SystemExit(0)

def _add_init_sweep(client, settings, owned, retired):
    """Retained-topic sweep and retired-entity removal for a collector with a defined init phase.

    owned: object ids or id prefixes this collector alone owns. A topic is in
    scope when it is <prefix>/<component>/<object>/<leaf> and <object> equals
    one of them or starts with one of them followed by "_".
    retired: "<component>/<object>" entities this collector used to publish.
    Every topic the collector publishes is recorded.

    init_done(): the collector calls it once it has published everything it
    expects for this run; only the first call does anything. It collects the
    retained topics with _scan_retained. Retained topics in scope or under a
    retired entity that were not published this run are stale; each retired
    entity's config topic not published this run is removed even when nothing
    is retained there (HA keeps non-retained discovery in memory).
    MQTT_SWEEP=dry prints them, apply clears them with clear_retained, off
    skips. No SUBACK (not connected) skips the sweep.
    """
    mode = settings["sweep"]
    if mode not in SWEEP_MODES:
        raise SystemExit(f"MQTT_SWEEP must be one of {', '.join(SWEEP_MODES)}, got {mode!r}")
    prefix = settings["prefix"]
    owned = tuple(owned)
    retired = tuple(retired)
    published = set()
    done = threading.Event()
    orig_publish = client.publish

    def publish(topic, *args, **kwargs):
        published.add(topic)
        return orig_publish(topic, *args, **kwargs)

    def init_done():
        if done.is_set():
            return
        done.set()
        if mode == "off":
            return
        retained = _scan_retained(client, prefix, lambda component, obj: owns(owned, obj) or f"{component}/{obj}" in retired)
        if retained is None:
            print("sweep: no SUBACK, skipped", flush=True)
            return
        stale = sorted(retained - published)
        removed = sorted({f"{prefix}/{r}/config" for r in retired} - published - retained)
        for topic in stale:
            print(f"sweep {mode}: {topic}", flush=True)
        for topic in removed:
            print(f"sweep {mode}: remove {topic}", flush=True)
        if mode == "apply":
            clear_retained(client, stale + removed)
        print(f"sweep {mode}: {len(stale)} stale retained topic(s), {len(removed)} retired entity config(s)", flush=True)

    client.publish = publish
    client.init_done = init_done


def create_client(client_id, settings, will_topic=None, will_payload="offline", replay_states=False, owned=(), entities=(), retired=()):
    if owned or retired:
        handle_cli(client_id, settings, entities, owned, retired)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.message_callback_add(f"{settings['prefix']}/status", on_ha_status)
    if settings["user"]:
        client.username_pw_set(settings["user"], settings["password"])
    if will_topic:
        client.will_set(will_topic, will_payload, qos=1)
        interval = settings.get("availability_heartbeat_sec", AVAILABILITY_HEARTBEAT_SEC)
        _start_availability_heartbeat(client, will_topic, will_payload, interval, replay_states)
    if owned or retired:
        _add_init_sweep(client, settings, owned, retired)
    return client
