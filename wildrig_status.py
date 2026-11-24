#!/usr/bin/env python3
"""WildRig Multi mining switch; per-GPU enable switch, intensity, hashrate, reject ratio, optional Pearl profitability factor → MQTT + HA."""
import json, os, time, http.client
from lib.mqtt_common import (
    get_machine_id, get_mqtt_settings, make_device, make_sensor_discovery, make_switch_discovery, make_number_discovery,
    create_client, slot_id, switch_mode
)
from lib.nvidia_common import gpu_device, gpu_inventory, gpu_stat_id
from lib.system_common import service_manager

SERVICE = "wildrig-multi"
ENV_FILE = "/etc/default/wildrig-multi"
GPU_METRICS = (
    ("switch", "wildrig_mining"), ("number", "wildrig_intensity"), ("sensor", "wildrig_hashrate"),
    ("sensor", "wildrig_reject_ratio"), ("sensor", "wildrig_profitability_factor"),
)
MACHINE_ID = get_machine_id()
DEVICE, DEVICE_ID = make_device()
CLIENT_ID = f"wildrig-{MACHINE_ID}"
settings = get_mqtt_settings()
PREFIX = settings["prefix"]
OBJ_SW = slot_id("wildrig_mining")
OBJ_AVAIL = slot_id("wildrig")

SERVICES = service_manager()
MODE = switch_mode("WILDRIG_SWITCH_MODE")
WILDRIG_HOST = os.getenv("WILDRIG_HOST", "127.0.0.1")
WILDRIG_PORT = int(os.getenv("WILDRIG_PORT", "60061"))
SPOT_TOPIC = os.getenv("ELECTRICITY_EUR_PER_KWH_TOPIC") or None
SPOT_FIXED = os.getenv("ELECTRICITY_EUR_PER_KWH")
SPOT_FIXED = float(SPOT_FIXED) if SPOT_FIXED not in (None, "") else None
PROFIT_WANTED = SPOT_TOPIC is not None or SPOT_FIXED is not None

AVAIL_T = f"{PREFIX}/sensor/{OBJ_AVAIL}/availability"
STATE_SW = f"{PREFIX}/switch/{OBJ_SW}/state"
CMD_SW = f"{PREFIX}/switch/{OBJ_SW}/set"
CONFIG_SW = f"{PREFIX}/switch/{OBJ_SW}/config"
DISCOVERY_SW = make_switch_discovery("WildRig Mining", STATE_SW, CMD_SW, AVAIL_T, OBJ_SW, DEVICE)
TOPIC_RATE = f"{PREFIX}/sensor/pearl_per_hs_day/state"
TOPIC_PEARL_USDT = f"{PREFIX}/sensor/pearl_usdt_price/state"
TOPIC_PEARL_USDT_ATTRS = f"{PREFIX}/sensor/pearl_usdt_price/attributes"
TOPIC_USDT_EUR = f"{PREFIX}/sensor/usdt_eur_price/state"
TOPIC_USDT_EUR_ATTRS = f"{PREFIX}/sensor/usdt_eur_price/attributes"

def gpu_card(g):
    device = gpu_device(g["uuid"], g["name"], DEVICE)
    t = {}
    for component, metric in GPU_METRICS:
        obj = gpu_stat_id(g["uuid"], g["name"], metric)
        t[metric] = {"obj": obj, "state": f"{PREFIX}/{component}/{obj}/state", "set": f"{PREFIX}/{component}/{obj}/set", "config": f"{PREFIX}/{component}/{obj}/config"}
    def disc(metric):
        return t[metric]["obj"], t[metric]["state"], AVAIL_T
    obj, state, avail = disc("wildrig_mining")
    t["wildrig_mining"]["discovery"] = make_switch_discovery("WildRig Enabled", state, t["wildrig_mining"]["set"], avail, obj, device)
    obj, state, avail = disc("wildrig_intensity")
    t["wildrig_intensity"]["discovery"] = make_number_discovery("WildRig Intensity", state, t["wildrig_intensity"]["set"], avail, obj, device, min_value=0, max_value=31, step=1, mode="slider")
    obj, state, avail = disc("wildrig_hashrate")
    t["wildrig_hashrate"]["discovery"] = make_sensor_discovery("WildRig Hashrate", state, avail, obj, device, unit="H/s", state_class="measurement", suggested_display_precision=0)
    obj, state, avail = disc("wildrig_reject_ratio")
    t["wildrig_reject_ratio"]["discovery"] = make_sensor_discovery("WildRig Reject Ratio", state, avail, obj, device, unit="%", state_class="measurement")
    obj, state, avail = disc("wildrig_profitability_factor")
    t["wildrig_profitability_factor"]["discovery"] = make_sensor_discovery("WildRig Profitability Factor", state, avail, obj, device, state_class="measurement", suggested_display_precision=3)
    return {"g": g, "t": t, "last_hr": 0.0, "last_power": 0.0, "pf_discovered": False}

CARDS = [gpu_card(g) for g in gpu_inventory()]
BY_BUS = {c["g"]["bus"]: c for c in CARDS}
COMMANDS = {c["t"][m]["set"]: (m, i) for i, c in enumerate(CARDS) for m in ("wildrig_mining", "wildrig_intensity")}

def read_env():
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            key, _, value = line.strip().lstrip("#").strip().partition("=")
            env[key.strip()] = value.strip()
    gpu_list = [int(v) for v in env["WILDRIG_GPU_LIST"].split(",") if v]
    listed = [int(v) for v in env["WILDRIG_INTENSITY"].split(",") if v]
    saved = [int(v) for v in env.get("WILDRIG_INTENSITY_ALL", "").split(",") if v.isdigit()]
    intensity = saved if len(saved) == len(CARDS) and all(v in range(32) for v in saved) else [0] * len(CARDS)
    if len(listed) == len(CARDS) != len(gpu_list):
        intensity = listed
    elif len(listed) == len(gpu_list) and all(i in range(len(CARDS)) for i in gpu_list):
        for i, v in zip(gpu_list, listed):
            intensity[i] = v
    if len(listed) not in (len(gpu_list), len(CARDS)) or not gpu_list or any(i not in range(len(CARDS)) for i in gpu_list) or any(v not in range(32) for v in intensity):
        raise SystemExit(f"{ENV_FILE}: WILDRIG_GPU_LIST needs at least one index below {len(CARDS)}, WILDRIG_INTENSITY one value 0..31 per listed GPU, in list order")
    return gpu_list, intensity

gpu_list, intensity = read_env()
conn = http.client.HTTPConnection(WILDRIG_HOST, WILDRIG_PORT, timeout=5)
rate = None
prices = {}
slippage = {}
spot = SPOT_FIXED
last_missing_print = 0.0
pending_main = None
pending_mining = {}
pending_intensity = {}
noted = set()

def note(message):
    if message not in noted:
        noted.add(message)
        print(f"wildrig_status: {message}", flush=True)

def summary():
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        data = resp.read()
    except (ConnectionError, http.client.HTTPException):
        conn.close()
        return None
    if resp.status != 200:
        raise SystemExit(f"WildRig HTTP {resp.status}")
    return json.loads(data)

def write_env(new_list, new_intensity):
    global gpu_list, intensity
    selected = [new_intensity[i] for i in new_list]
    args = f"--gpu-list {','.join(map(str, new_list))}"
    if any(selected):
        args += f" --gpu-intensity {','.join(str(v) if v else '*' for v in selected)}"
    tmp = f"{ENV_FILE}.tmp"
    with open(tmp, "w") as f:
        f.write(f"WILDRIG_GPU_LIST={','.join(map(str, new_list))}\nWILDRIG_INTENSITY={','.join(map(str, selected))}\n")
        if len(new_list) < len(CARDS):
            f.write(f"# WILDRIG_INTENSITY_ALL={','.join(map(str, new_intensity))}\n")
        f.write(f"WILDRIG_ARGS={args}\n")
    os.replace(tmp, ENV_FILE)
    gpu_list, intensity = new_list, new_intensity

def mining_set(active):
    return set(gpu_list) if active else set()

def flush_pending():
    """Apply the commands of the last second with at most one restart. The main switch is the unit (OFF stops it,
    ON starts it with the enabled GPUs); a GPU switch only edits the enabled list, which survives the main switch.
    The list never goes empty (the miner would take every GPU): the last enabled GPU's OFF is ignored."""
    global pending_main
    if pending_main is None and not pending_mining and not pending_intensity:
        return
    active = SERVICES.is_active(SERVICE)
    run = active if pending_main is None else pending_main
    pending_main = None
    new_intensity = list(intensity)
    for i in list(pending_intensity):
        new_intensity[i] = max(0, min(31, pending_intensity.pop(i)))
    selected = set(gpu_list)
    for i in list(pending_mining):
        (selected.add if pending_mining.pop(i) else selected.discard)(i)
    if not selected:
        print(f"wildrig_status: ignored turning off the last enabled GPU {gpu_list}; use the WildRig Mining switch", flush=True)
        selected = set(gpu_list)
    restart = active and run and (selected != set(gpu_list) or any(new_intensity[i] != intensity[i] for i in selected))
    write_env(sorted(selected), new_intensity)
    if active and (not run or restart):
        SERVICES.stop(SERVICE)
    if run and (not active or restart):
        SERVICES.start(SERVICE)

def hashrate(avgs):
    return float(avgs[1] or avgs[0] or 0)

def revenue_eur_per_hour(hr):
    pearl_eur = prices["pearl_usdt"] * (1 - slippage["pearl_usdt"] / 100.0) * prices["usdt_eur"] * (1 - slippage["usdt_eur"] / 100.0)
    return hr * rate / 24.0 * pearl_eur

def missing_inputs(card):
    missing = []
    if rate is None:
        missing.append(f"mqtt {TOPIC_RATE}")
    for pair, topic in (("pearl_usdt", TOPIC_PEARL_USDT), ("usdt_eur", TOPIC_USDT_EUR)):
        if pair not in prices:
            missing.append(f"mqtt {topic}")
        if pair not in slippage:
            missing.append(f"mqtt {topic.rsplit('/', 1)[0]}/attributes")
    if spot is None or spot <= 0:
        missing.append(f"mqtt {SPOT_TOPIC}" if SPOT_TOPIC else "ELECTRICITY_EUR_PER_KWH")
    if card["last_hr"] <= 0:
        missing.append(f"GPU {card['g']['idx']} hashrate (live or last mining)")
    if card["last_power"] <= 0:
        missing.append(f"GPU {card['g']['idx']} hwmon.power (live or last mining)")
    return missing

def publish_profit(card, now):
    global last_missing_print
    missing = missing_inputs(card)
    t = card["t"]["wildrig_profitability_factor"]
    if missing:
        if now - last_missing_print >= 60.0:
            print(f"wildrig_status: profitability waiting on: {', '.join(missing)}", flush=True)
            last_missing_print = now
        return
    factor = revenue_eur_per_hour(card["last_hr"]) / (card["last_power"] / 1000.0 * spot)
    if not card["pf_discovered"]:
        client.publish(t["config"], json.dumps(t["discovery"]))
        card["pf_discovered"] = True
        print(f"wildrig_status: GPU {card['g']['idx']} profitability factor online (value {factor:.6g})", flush=True)
    client.publish(t["state"], format(factor, ".15g"))

def publish_stats():
    active = SERVICES.is_active(SERVICE)
    mining = mining_set(active)
    data = summary() if active else None
    live = {}
    if data is not None:
        hw, res, threads = data.get("hwmon") or {}, data["results"], data["hashrate"]["threads"]
        buses, watts = hw.get("busID") or [], hw.get("power") or []
        accepted, rejected = res.get("shares_accepted") or [], res.get("shares_rejected") or []
        if not len(buses) == len(threads) == len(watts) == len(accepted) == len(rejected):
            note(f"API per-GPU arrays differ in length (busID {len(buses)}, threads {len(threads)}, power {len(watts)}, shares {len(accepted)}/{len(rejected)}), per-GPU values skipped")
            buses = []
        for k, bus in enumerate(buses):
            card = BY_BUS.get(bus)
            if card is None:
                note(f"API GPU {k} on bus {bus} matches no nvidia-smi GPU (starting up?), skipped")
            elif card["g"]["idx"] not in mining:
                note(f"API reports bus {bus} (GPU {card['g']['idx']}), not in WILDRIG_GPU_LIST {gpu_list}: shown as disabled (the API lists every GPU, or WildRig numbers GPUs differently from nvidia-smi)")
            else:
                acc, rej = int(accepted[k]), int(rejected[k])
                live[bus] = (hashrate(threads[k]), float(watts[k]), 0.0 if acc + rej == 0 else rej * 100.0 / (acc + rej))
    client.publish(STATE_SW, "ON" if active else "OFF")
    now = time.monotonic()
    for card in CARDS:
        g, t = card["g"], card["t"]
        on = g["idx"] in mining
        client.publish(t["wildrig_mining"]["state"], "ON" if g["idx"] in gpu_list else "OFF")
        client.publish(t["wildrig_intensity"]["state"], str(intensity[g["idx"]]))
        if g["bus"] in live:
            hr, power, rej = live[g["bus"]]
            client.publish(t["wildrig_hashrate"]["state"], format(hr, ".15g"))
            client.publish(t["wildrig_reject_ratio"]["state"], format(rej, ".15g"))
            if hr > 0:
                card["last_hr"] = hr
                if power > 0:
                    card["last_power"] = power
        elif not on:
            client.publish(t["wildrig_hashrate"]["state"], "0")
        if PROFIT_WANTED:
            publish_profit(card, now)

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe(f"{PREFIX}/status")
        client.publish(CONFIG_SW, json.dumps(DISCOVERY_SW))
        for card in CARDS:
            for _, metric in GPU_METRICS:
                t = card["t"][metric]
                if metric != "wildrig_profitability_factor" or card["pf_discovered"]:
                    client.publish(t["config"], json.dumps(t["discovery"]))
        for topic in (CMD_SW, *COMMANDS):
            client.subscribe(topic)
        if PROFIT_WANTED:
            for topic in (TOPIC_RATE, TOPIC_PEARL_USDT, TOPIC_PEARL_USDT_ATTRS, TOPIC_USDT_EUR, TOPIC_USDT_EUR_ATTRS):
                client.subscribe(topic)
            if SPOT_TOPIC:
                client.subscribe(SPOT_TOPIC)
        client.publish(AVAIL_T, "online")

def on_message(client, userdata, msg):
    global rate, spot, pending_main
    topic = msg.topic
    payload = msg.payload.decode()
    if topic == CMD_SW:
        if MODE != "control":
            print(f"wildrig_status: ignored {payload!r} on {topic} (mode={MODE})", flush=True)
        elif payload in ("ON", "OFF"):
            pending_main = payload == "ON"
        return
    if topic in COMMANDS:
        metric, i = COMMANDS[topic]
        if MODE != "control":
            print(f"wildrig_status: ignored {payload!r} on {topic} (mode={MODE})", flush=True)
        elif metric == "wildrig_intensity":
            pending_intensity[i] = int(float(payload))
        elif payload in ("ON", "OFF"):
            pending_mining[i] = payload == "ON"
        return
    if topic == TOPIC_RATE:
        rate = float(payload)
    elif topic == TOPIC_PEARL_USDT:
        prices["pearl_usdt"] = float(payload)
    elif topic == TOPIC_USDT_EUR:
        prices["usdt_eur"] = float(payload)
    elif topic == TOPIC_PEARL_USDT_ATTRS:
        slippage["pearl_usdt"] = float(json.loads(payload)["slippage_pct"])
    elif topic == TOPIC_USDT_EUR_ATTRS:
        slippage["usdt_eur"] = float(json.loads(payload)["slippage_pct"])
    elif SPOT_TOPIC and topic == SPOT_TOPIC:
        spot = float(payload)

print(
    f"wildrig_status: machine={MACHINE_ID} mode={MODE} profit_wanted={PROFIT_WANTED} api={WILDRIG_HOST}:{WILDRIG_PORT} "
    f"gpus={[(c['g']['idx'], c['g']['name'], c['g']['bus']) for c in CARDS]} gpu_list={gpu_list} intensity={intensity}",
    flush=True,
)
if not PROFIT_WANTED:
    print("wildrig_status: basic stats only (no spot topic / fixed EUR per kWh)", flush=True)

client = create_client(CLIENT_ID, settings, will_topic=AVAIL_T)
client.on_connect = on_connect
client.on_message = on_message
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()

try:
    while True:
        flush_pending()
        publish_stats()
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
finally:
    client.publish(AVAIL_T, "offline")
    client.loop_stop()
    client.disconnect()
    conn.close()
