#!/usr/bin/env python3
"""Generic shared MQTT + Home Assistant helpers: settings, identity (slot ids), discovery, client + availability, retained sweep, purge and the collector CLI.
Family-specific helpers live in lib/<family>_common.py."""
import argparse, os, re, json, socket, sys, threading, time
import paho.mqtt.client as mqtt

# Non-retained availability republish while a collector is connected.
# After a purge, HA entities recover within this interval without a restart.
AVAILABILITY_HEARTBEAT_SEC = 60
DISCOVERY_SETTLE_SEC = 2
SWEEP_SUBACK_TIMEOUT_SEC = 10
SWEEP_MODES = ("off", "dry", "apply")
ONLY_LABELS = ("current", "stale")


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

def slot_lists(entities, availability=()):
    """owned / entities for create_client or handle_cli, from the same names as the published ids.

    entities: "<component>/<name>" fixed-name entities, published as
    <component>/slot_id(name). availability: availability object names.
    """
    pairs = [e.split("/", 1) for e in entities]
    return {
        "owned": tuple(slot_id(n) for n in availability) + tuple(slot_id(n) for _, n in pairs),
        "entities": tuple(f"{c}/{slot_id(n)}" for c, n in pairs),
    }

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

def make_device(kind="linux"):
    """The machine's HA device <kind>_host_<machine_id> (case kept), named machine_name(), with the hostname as model when it differs."""
    machine_id = get_machine_id()
    device_id = f"{kind}_host_{machine_id}"
    device = {"identifiers": [device_id], "name": machine_name()}
    if machine_id != get_hostname():
        device["model"] = get_hostname()
    return device, device_id

def make_linked_device(identifier, name, via_device_id, **info):
    """HA device for hardware that can move between machines (peripheral battery, GPU, drive): keyed by the
    hardware (identifier, no slot or hostname), linked to the machine it is in now via via_device. info: optional
    manufacturer / model / serial_number, empty ones dropped."""
    device = {"identifiers": [identifier], "name": name, "via_device": via_device_id}
    device.update({k: v for k, v in info.items() if v})
    return device

def machine_count(prefix, availability_topic, name, metric, device):
    """Diagnostic count sensor <slot>_<metric> on the machine device, for collectors whose other entities sit on
    linked devices. It defines the via_device itself: HA stub-creates a via_device it does not know yet, without a
    name, so it shows the MQTT entry title (the broker address) until something names it."""
    obj = slot_id(metric)
    state = f"{prefix}/sensor/{obj}/state"
    disc = make_sensor_discovery(name, state, availability_topic, obj, device, state_class="measurement", entity_category="diagnostic")
    return {"obj": obj, "state": state, "config": f"{prefix}/sensor/{obj}/config", "discovery": disc}

def linked_attributes(machine_id):
    """Retained attributes of a linked device's entity: the machine it was last linked to (see AGENT.md, linked devices)."""
    return {"linked_machine": machine_id, "linked_hostname": get_hostname()}

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

def _scan_retained(client, prefix, match, payloads=None):
    """Retained topics <prefix>/<component>/<object>/<leaf> for which match(component, object) is true.

    Subscribes to <prefix>/+/+/+, waits for the SUBACK, then DISCOVERY_SETTLE_SEC
    for the broker to deliver retained messages, and unsubscribes. payloads, a
    dict, also gets each found topic's payload. Returns None
    when there is no SUBACK (not connected).
    """
    scope = f"{prefix}/+/+/+"
    found = set()
    subacked = threading.Event()

    def on_retained(client, userdata, msg):
        component, obj = msg.topic.split("/")[-3:-1]
        if msg.retain and msg.payload and match(component, obj):
            found.add(msg.topic)
            if payloads is not None:
                payloads[msg.topic] = msg.payload

    client.on_subscribe = lambda *a: subacked.set()
    client.message_callback_add(scope, on_retained)
    client.subscribe(scope)
    if subacked.wait(SWEEP_SUBACK_TIMEOUT_SEC):
        time.sleep(DISCOVERY_SETTLE_SEC)
    client.unsubscribe(scope)
    client.message_callback_remove(scope)
    client.on_subscribe = None
    return set(found) if subacked.is_set() else None

def force_purge(client_id, settings, entities=(), owned=(), apply=False, match=None, stale=False, only=None):
    """List (apply=False) or delete (apply=True) a collector's entities and retained topics.

    entities are "<component>/<object>"; owned are object ids or id prefixes as
    for the sweep (an exact owned id, e.g. the availability object, is
    current); match(object, linked) optionally selects more objects, each a
    whole entity whose config topic is included too; linked is the
    linked_machine in the object's retained attributes (linked devices), else
    None. The topics are every entity config topic plus every retained topic
    under an owned, entity or matched object. Each is labelled current (an
    entity or exact owned id) or stale (in scope, not current: hardware that is
    gone). only (current or stale) prints just that label; the summary counts
    objects per label over everything. stale=True deletes only stale topics:
    current topics are never touched, so it is safe while the collector runs.
    Otherwise apply deletes all of them with clear_retained (an empty config
    removes the HA entity and its history, retained or not). Independent of
    MQTT_SWEEP. Uses its own short-lived connection; exits when it cannot
    connect.
    """
    prefix = settings["prefix"]
    named = set(entities)
    scope = tuple(owned)
    current = {e.split("/", 1)[1] for e in entities} | set(owned)
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
        payloads = {}
        listed = (lambda component, obj: True) if match else (lambda component, obj: owns(scope, obj) or f"{component}/{obj}" in named)
        everything = _scan_retained(client, prefix, listed, payloads)
        if everything is None:
            raise SystemExit("purge: no SUBACK")
        links = {}
        for t, p in payloads.items():
            if t.endswith("/attributes"):
                try:
                    links[t.split("/")[-2]] = json.loads(p).get("linked_machine")
                except (ValueError, AttributeError):
                    pass
        matched = lambda obj: bool(match and match(obj, links.get(obj)))
        retained = {t for t in everything if owns(scope, t.split("/")[-2]) or "/".join(t.split("/")[-3:-1]) in named or matched(t.split("/")[-2])}
        found = {"/".join(t.split("/")[-3:-1]) for t in retained if matched(t.split("/")[-2])}
        topics = sorted({f"{prefix}/{e}/config" for e in named | found} | retained)
        kind = lambda topic: "current" if topic.split("/")[-2] in current else "stale"
        targets = [t for t in topics if not (stale and kind(t) == "current")]
        shown = [t for t in topics if not only or kind(t) == only]
        for topic in shown:
            action = ("purge" if topic in targets else "keep") if apply else "list"
            print(f"{action}: {topic} [{kind(topic)}]{' (retained)' if topic in retained else ''}", flush=True)
        if apply:
            clear_retained(client, targets)
            print(f"purged {len(targets)} topic(s), {len(retained)} retained, {len(topics) - len(targets)} kept", flush=True)
        else:
            objs = {}
            for t in topics:
                objs.setdefault(kind(t), set()).add(t.split("/")[-2])
            print(f"summary: {len(objs.get('current', ()))} current, {len(objs.get('stale', ()))} stale", flush=True)
            print(f"topics: {len(shown)} listed{f' (--only {only})' if only else ''}, {len(retained)} retained", flush=True)
        return targets
    finally:
        client.loop_stop()
        client.disconnect()

def handle_cli(client_id, settings, entities=(), owned=(), match=None, present=None):
    """The collector CLI shared by every script; returns when no flag is given.

    --list (read-only, each topic labelled current / stale), --purge
    (everything; stop the collector first) and --purge-stale (only stale;
    current ids are never touched, safe while the collector runs) go through
    force_purge and exit 0. present() -> ({object: label}, [unknown]) adds
    objects of devices present now (drives) to the current entities; with any
    unknown entry --purge-stale refuses (exit 1). --claim answers
    mqtt_purge_retained.py --inventory. --help prints the usage and exits 0, an
    unknown flag exits 2. Runs before the collector creates its client or
    opens a source. entities may be a callable, called only for the flags
    (hardware-keyed ids that need a scan).
    """
    parser = argparse.ArgumentParser(prog=os.path.basename(sys.argv[0]), description="Without a flag, runs the collector. Flags exit without starting it.")
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument("--list", action="store_true", help="read-only: print every entity and retained topic this script publishes (current) and retained leftovers in its scope, e.g. of hardware that is gone (stale)")
    flags.add_argument("--purge", action="store_true", help="remove all of them from HA and the broker; stop the collector first")
    flags.add_argument("--purge-stale", action="store_true", help="remove only the stale ones; current ids (config, state, availability) are never touched, so it is safe while the collector runs")
    flags.add_argument("--claim", action="store_true", help="read object ids on stdin and print, as JSON, those this script publishes now (current) or has in scope (scope); no connection (mqtt_purge_retained.py --inventory)")
    parser.add_argument("--only", choices=ONLY_LABELS, help="with --list: print only current or stale topics")
    args = parser.parse_args()
    if args.only and not args.list:
        parser.error("--only needs --list")
    if not (args.list or args.purge or args.purge_stale or args.claim):
        return
    entities = entities() if callable(entities) else entities
    keep, unknown = present() if present else ({}, [])
    entities = tuple(entities) + tuple(f"sensor/{o}" for o in sorted(keep))
    if args.claim:
        current = {e.split("/", 1)[1] for e in entities} | set(owned)
        claims = {}
        for obj in sys.stdin.read().split():
            if obj in current:
                claims[obj] = "current"
            elif owns(tuple(owned), obj) or (match and match(obj, None)):
                claims[obj] = "scope"
        print(json.dumps(claims), flush=True)
        raise SystemExit(0)
    for obj, label in sorted(keep.items()):
        print(f"present: {obj} ({label})", flush=True)
    for u in unknown:
        print(f"unknown: {u}", flush=True)
    if args.purge_stale and unknown:
        raise SystemExit("purge-stale: refused, a present device's object id is unknown without waking it; remove single ghosts with mqtt_purge_retained.py --entity")
    if args.purge:
        print("purge: stop this collector first, or its next publish recreates the entities", flush=True)
    force_purge(f"{client_id}-purge", settings, entities, owned, apply=not args.list, match=match, stale=args.purge_stale, only=args.only)
    raise SystemExit(0)

def _add_init_sweep(client, settings, owned):
    """Retained-topic sweep for a collector with a defined init phase.

    owned: object ids or id prefixes this collector alone owns. A topic is in
    scope when it is <prefix>/<component>/<object>/<leaf> and <object> equals
    one of them or starts with one of them followed by "_". Every topic the
    collector publishes is recorded.

    init_done(): the collector calls it once it has published everything it
    expects for this run; only the first call does anything. It collects the
    retained topics in scope with _scan_retained; those not published this run
    are stale. MQTT_SWEEP=dry prints them, apply clears them with
    clear_retained, off skips. No SUBACK (not connected) skips the sweep.
    """
    mode = settings["sweep"]
    if mode not in SWEEP_MODES:
        raise SystemExit(f"MQTT_SWEEP must be one of {', '.join(SWEEP_MODES)}, got {mode!r}")
    prefix = settings["prefix"]
    owned = tuple(owned)
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
        retained = _scan_retained(client, prefix, lambda component, obj: owns(owned, obj))
        if retained is None:
            print("sweep: no SUBACK, skipped", flush=True)
            return
        stale = sorted(retained - published)
        for topic in stale:
            print(f"sweep {mode}: {topic}", flush=True)
        if mode == "apply":
            clear_retained(client, stale)
        print(f"sweep {mode}: {len(stale)} stale retained topic(s)", flush=True)

    client.publish = publish
    client.init_done = init_done


def create_client(client_id, settings, will_topic=None, will_payload="offline", replay_states=False, owned=(), entities=()):
    if owned:
        handle_cli(client_id, settings, entities, owned)
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    client.message_callback_add(f"{settings['prefix']}/status", on_ha_status)
    if settings["user"]:
        client.username_pw_set(settings["user"], settings["password"])
    if will_topic:
        client.will_set(will_topic, will_payload, qos=1)
        interval = settings.get("availability_heartbeat_sec", AVAILABILITY_HEARTBEAT_SEC)
        _start_availability_heartbeat(client, will_topic, will_payload, interval, replay_states)
    if owned:
        _add_init_sweep(client, settings, owned)
    return client
