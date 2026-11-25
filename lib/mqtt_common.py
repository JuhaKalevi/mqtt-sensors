#!/usr/bin/env python3
"""Generic shared MQTT + Home Assistant helpers: settings, identity (slot ids), discovery, client + availability and rediscovery.
Family-specific helpers live in lib/<family>_common.py."""
import os, re, socket, threading
import paho.mqtt.client as mqtt

DISCOVERY_SETTLE_SEC = 2


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

def id_slug(text):
    """Lowercase slug [a-z0-9_]: only for HA entity ids (entity_id()), which cannot hold uppercase."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

def id_part(text):
    """Case-preserving slug [A-Za-z0-9_] for unique_ids and topics, which HA and MQTT accept in any case."""
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")

def entity_id(platform, unique_id):
    """default_entity_id: <platform>.<unique_id> lowercased (HA entity ids are lowercase only). The one place topic id and entity id may differ, by case."""
    return f"{platform}.{id_slug(unique_id)}"

def get_machine_id():
    """The slot of this machine, case kept: MACHINE_ID from .env exactly as written (Finnish area + machine, e.g. Olohuone_PC1), else the hostname as-is."""
    load_dotenv()
    machine_id = os.getenv("MACHINE_ID") or ""
    if not machine_id:
        return get_hostname()
    if not re.fullmatch(r"[A-Za-z0-9]+(_[A-Za-z0-9]+)*", machine_id):
        raise SystemExit(f"MACHINE_ID must be letters, digits and single underscores (e.g. Olohuone_PC1), got {machine_id!r}")
    return machine_id

def machine_name():
    """Display name of the machine: MACHINE_ID with underscores as spaces (Olohuone_PC1 -> "Olohuone PC1"), else the hostname as-is."""
    return get_machine_id().replace("_", " ") if os.getenv("MACHINE_ID") else get_hostname()

def slot_id(name):
    """Object id of a fixed-name host entity (or availability object): <machine_id>_<name>."""
    return f"{get_machine_id()}_{name}"

def get_mqtt_settings():
    load_dotenv()
    return {
        "host": os.getenv("MQTT_HOST", "localhost"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
        "user": os.getenv("MQTT_USER") or None,
        "password": os.getenv("MQTT_PASS") or None,
        "prefix": os.getenv("MQTT_PREFIX", "homeassistant"),
    }

def make_device(kind="linux"):
    """The machine's HA device <kind>_host_<machine_id> (case kept), named machine_name(), with manufacturer
    and model from DMI (Linux) or WMI (Windows). The hostname is the model only when those are empty and it
    differs from the slot."""
    from lib.board_common import machine_hardware_info
    machine_id = get_machine_id()
    device_id = f"{kind}_host_{machine_id}"
    device = {"identifiers": [device_id], "name": machine_name()}
    manufacturer, model = machine_hardware_info(kind)
    if not model and get_hostname() != machine_id:
        model = get_hostname()
    if manufacturer:
        device["manufacturer"] = manufacturer
    if model:
        device["model"] = model
    return device, device_id

def make_child_device(parent_device, identifier, name):
    """HA child device (HA 2026.11+, MQTT `child_device`): a part of the parent device, with its own name and entities and
    the parent's area. Only identifiers and name; it cannot move to another parent."""
    return {**parent_device, "child_device": {"identifiers": [identifier], "name": name}}

def machine_count(prefix, availability_topic, name, metric, device):
    """Diagnostic count sensor <slot>_<metric> on the machine device, for collectors whose other entities sit on
    child devices."""
    obj = slot_id(metric)
    state = f"{prefix}/sensor/{obj}/state"
    disc = make_sensor_discovery(name, state, availability_topic, obj, device, state_class="measurement", entity_category="diagnostic")
    return {"obj": obj, "state": state, "config": f"{prefix}/sensor/{obj}/config", "discovery": disc}

def _availability(availability_topic):
    if not availability_topic:
        return {}
    return {"availability_topic": availability_topic, "payload_available": "online", "payload_not_available": "offline"}

def make_sensor_discovery(name, state_topic, availability_topic, unique_id, device,
                          unit=None, device_class=None, state_class=None, **extra):
    """Sensor discovery. availability_topic None: no availability (run-once). unit / device_class / state_class
    are left out when None. extra: more discovery keys (json_attributes_topic, suggested_display_precision,
    entity_category, ...)."""
    optional = {"unit_of_measurement": unit, "device_class": device_class, "state_class": state_class}
    return {
        "name": name,
        "state_topic": state_topic,
        **_availability(availability_topic),
        **{k: v for k, v in optional.items() if v is not None},
        "unique_id": unique_id,
        "default_entity_id": entity_id("sensor", unique_id),
        "device": device,
        **extra,
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

def make_switch_discovery(name, state_topic, command_topic, availability_topic, unique_id, device, **extra):
    return {
        "name": name,
        "state_topic": state_topic,
        "command_topic": command_topic,
        **_availability(availability_topic),
        "payload_on": "ON",
        "payload_off": "OFF",
        "unique_id": unique_id,
        "default_entity_id": entity_id("switch", unique_id),
        "device": device,
        **extra,
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
        "default_entity_id": entity_id("number", unique_id),
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


def _repeat_online(client, will_topic, will_payload, replay_states=False):
    """Repeat non-retained 'online' on will_topic DISCOVERY_SETTLE_SEC after each 'online' the collector publishes.

    on_connect publishes discovery then 'online' (on connect, HA 'online' and rediscovery); HA subscribes to an
    entity's topics only once it has processed its discovery, so that first 'online' is usually lost. With
    replay_states, the last non-retained payload of every non-config topic is repeated with it, for collectors that
    publish slowly. Several 'online' within the delay give one repeat. Publishing the will payload (usually
    'offline') or disconnect() cancels it and stops repeats, so a shutdown cannot be followed by a late 'online'.
    """
    lock = threading.Lock()
    orig_publish = client.publish
    orig_disconnect = client.disconnect
    offline = _payload_bytes(will_payload)
    last = {}
    state = {"timer": None, "stopped": False}

    def cancel():
        state["stopped"] = True
        if state["timer"]:
            state["timer"].cancel()

    def repeat():
        with lock:
            if state["stopped"] or not client.is_connected():
                return
            orig_publish(will_topic, "online")  # not retained
            for topic, payload in list(last.items()):
                orig_publish(topic, payload)  # not retained

    def publish(topic, payload=None, *args, **kwargs):
        with lock:
            if topic == will_topic and _payload_bytes(payload) == offline:
                cancel()
            elif topic == will_topic and _payload_bytes(payload) == b"online":
                state["stopped"] = False
                if state["timer"]:
                    state["timer"].cancel()
                state["timer"] = threading.Timer(DISCOVERY_SETTLE_SEC, repeat)
                state["timer"].daemon = True
                state["timer"].start()
            elif replay_states and not topic.endswith("/config") and not _retain(args, kwargs):
                last[topic] = payload
        return orig_publish(topic, payload, *args, **kwargs)

    def disconnect(*args, **kwargs):
        with lock:
            cancel()
        return orig_disconnect(*args, **kwargs)

    client.publish = publish
    client.disconnect = disconnect


def _watch_discovery(client, prefix):
    """Republish discovery when Home Assistant clears it (rediscovery).

    Deleting an MQTT entity or device in HA publishes a zero-length retained payload to each of its config topics.
    Every config topic this collector publishes non-empty is subscribed; a zero-length payload arriving on one of
    them re-runs on_connect (all current discovery plus 'online', as on HA 'online') DISCOVERY_SETTLE_SEC after the
    last one, so deleting a device gives one republish. A config topic the collector clears itself (hardware that
    left) is dropped from the watch first, so its own clear never triggers it.
    """
    orig_publish = client.publish
    lock = threading.Lock()
    watched = set()
    timer = [None]

    def publish(topic, payload=None, *args, **kwargs):
        config = topic.startswith(prefix + "/") and topic.endswith("/config")
        if config and not _payload_bytes(payload):
            with lock:
                watched.discard(topic)
            client.unsubscribe(topic)
        info = orig_publish(topic, payload, *args, **kwargs)
        if config and _payload_bytes(payload):
            with lock:
                watched.add(topic)
            client.subscribe(topic)
        return info

    def republish():
        if client.is_connected() and client.on_connect:
            print("rediscovery: discovery cleared in Home Assistant, republishing", flush=True)
            client.on_connect(client, None, None, 0, None)

    def on_config(client, userdata, msg):
        if msg.payload:
            return
        with lock:
            if msg.topic not in watched:
                return
            if timer[0]:
                timer[0].cancel()
            timer[0] = threading.Timer(DISCOVERY_SETTLE_SEC, republish)
            timer[0].daemon = True
            timer[0].start()

    client.publish = publish
    client.message_callback_add(f"{prefix}/+/+/config", on_config)


def create_client(client_id, settings, will_topic=None, will_payload="offline", replay_states=False):
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.message_callback_add(f"{settings['prefix']}/status", on_ha_status)
    _watch_discovery(client, settings["prefix"])
    if settings["user"]:
        client.username_pw_set(settings["user"], settings["password"])
    if will_topic:
        client.will_set(will_topic, will_payload, qos=1)
        _repeat_online(client, will_topic, will_payload, replay_states)
    return client
