"""NVIDIA GPU ids and devices shared by nvidia_gpu_power.py, windows/nvidia_gpu_power.py and wildrig_status.py.

Power and energy are slot-keyed on the machine device (<slot>_gpu<N>_power / _energy, generic names), so the history
of a slot spans GPU swaps for power tracking. The health stats follow the card: keyed by its UUID plus a short model
name for readability (gpu_<uuid>_<Model>_<metric>, e.g. gpu_8b1c…_RTX4080_temperature), on its own HA device linked
to the machine.
"""
import json, re, subprocess
from lib.mqtt_common import (
    id_slug, slot_id, make_linked_device, make_power_discovery, make_energy_discovery, make_sensor_discovery
)

VRAM_NEAR_FULL = 90.0
# Names as listed by `nvidia-smi --help-query-gpu`. One unknown name makes nvidia-smi reject the whole query
# (no CSV at all), so only add a field after checking it there. There is no PCIe replay counter field.
SLOWDOWN_FIELDS = (
    "clocks_event_reasons.hw_slowdown",
    "clocks_event_reasons.hw_thermal_slowdown",
    "clocks_event_reasons.hw_power_brake_slowdown",
    "clocks_event_reasons.sw_thermal_slowdown",
)
STALL_FIELDS = ["pcie.link.gen.gpucurrent", "pcie.link.width.current", *SLOWDOWN_FIELDS]

def clean_gpu_name(name):
    name = re.sub(r"^NVIDIA\s+", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "GPU"

def short_model(name):
    """nvidia-smi name without "NVIDIA GeForce " and spaces, case kept: "NVIDIA GeForce RTX 4080" -> "RTX4080"."""
    return re.sub(r"[^A-Za-z0-9]+", "", re.sub(r"^(NVIDIA\s+)?(GeForce\s+)?", "", name.strip(), flags=re.I)) or "GPU"

def gpu_device_id(uuid):
    """HA device identifier of a GPU: its nvidia-smi UUID ("GPU-…"), slugged."""
    return id_slug(uuid)

def gpu_stat_id(uuid, name, metric):
    """GPU health stat: gpu_<uuid>_<short model>_<metric>."""
    return f"{gpu_device_id(uuid)}_{short_model(name)}_{metric}"

def gpu_device(uuid, name, machine_device):
    """The GPU's linked HA device (keyed by its UUID, via_device = the machine it is in now)."""
    return make_linked_device(gpu_device_id(uuid), name, machine_device["identifiers"][0], manufacturer="NVIDIA", model=name)

def gpu_inventory():
    """One nvidia-smi query: [{"idx", "uuid", "name", "bus"}] in nvidia-smi index order; bus is the PCI bus number."""
    out = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,name,pci.bus_id", "--format=csv,noheader"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    rows = [[p.strip() for p in line.split(",")] for line in out.splitlines() if line.count(",") == 3]
    return [{"idx": int(r[0]), "uuid": r[1], "name": clean_gpu_name(r[2]), "bus": int(r[3].split(":")[1], 16)} for r in rows]

def parse_line(line):
    """One CSV row of the nvidia_gpu_power query (index, name, power.draw, …, uuid), or None for anything else."""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 4:
        return None
    try:
        idx = int(parts[0])
    except ValueError:
        return None
    try:
        power = float(parts[2])
    except ValueError:
        power = 0.0
    return {"idx": idx, "name": clean_gpu_name(parts[1]), "power": power, "uuid": parts[-1], "extra": parts[3:-1]}

def read_n(proc, n):
    """The next n rows (one sample of n GPUs) from a `--loop` stream; None at EOF."""
    rows = []
    while len(rows) < n:
        line = proc.stdout.readline()
        if not line:
            return None
        row = parse_line(line.strip())
        if row:
            rows.append(row)
    return rows

def no_data(rc, output):
    """Exit message when nvidia-smi gave no rows, with its exit code and output (e.g. an unknown query field)."""
    return f"nvidia-smi produced no data (rc={rc}): {' '.join(output.split())[:500] or 'no output'}"

def stall_fields(stats):
    """nvidia-smi fields for PCIe link and throttle slowdown, only with NVIDIA_GPU_STATS."""
    return list(STALL_FIELDS) if stats else []

def mem_total_index(stats):
    """Index of memory.total in the sample's extra list (after STATS and stall fields)."""
    return len(stats) + (len(STALL_FIELDS) if stats else 0)

def stat_value(stats, extra, i):
    if stats[i][0] == "memory.used":
        return str(float(extra[i]) / float(extra[mem_total_index(stats)]) * 100)
    return extra[i]

def _ok(val):
    return bool(val) and not val.startswith("[")

def stall_field(extra, stats, name):
    if not stats:
        return None
    try:
        i = len(stats) + STALL_FIELDS.index(name)
    except ValueError:
        return None
    if i >= len(extra):
        return None
    val = extra[i]
    return val if _ok(val) else None

def _reason_active(val):
    return val is not None and val.strip().lower() == "active"

def _stall_entity(prefix, availability_topic, g, device, metric, title, unit, device_class, state_class):
    obj = gpu_stat_id(g["uuid"], g["name"], metric)
    state = f"{prefix}/sensor/{obj}/state"
    attrs = f"{prefix}/sensor/{obj}/attributes"
    disc = make_sensor_discovery(
        title, state, availability_topic, obj, device, unit, device_class, state_class,
        json_attributes_topic=attrs, entity_category="diagnostic")
    return {"state": state, "config": f"{prefix}/sensor/{obj}/config", "disc": disc, "attrs": attrs}

def gpu_entities(prefix, availability_topic, g, stats, machine_device, multi):
    """Topics and discovery of one GPU sample row g (idx, name, uuid, extra): power and energy on the machine device
    (generic slot ids, named by the current card's model: "GeForce RTX 4080 Power", "GeForce RTX 4080 (GPU <N>) Power"
    when the machine has several), stats on the GPU's own linked device (named by the same model)."""
    device = gpu_device(g["uuid"], g["name"], machine_device)

    def topics(metric):
        obj = gpu_stat_id(g["uuid"], g["name"], metric)
        return obj, f"{prefix}/sensor/{obj}/state", f"{prefix}/sensor/{obj}/config"

    def slot_topics(metric):
        obj = slot_id(f"gpu{g['idx']}_{metric}")
        return obj, f"{prefix}/sensor/{obj}/state", f"{prefix}/sensor/{obj}/config"

    base = f"{g['name']} (GPU {g['idx']})" if multi else g["name"]
    obj_p, state_p, config_p = slot_topics("power")
    obj_e, state_e, config_e = slot_topics("energy")
    entry = {
        "idx": g["idx"], "state_p": state_p, "state_e": state_e, "config_p": config_p, "config_e": config_e,
        "disc_p": make_power_discovery(f"{base} Power", state_p, availability_topic, obj_p, machine_device),
        "disc_e": make_energy_discovery(f"{base} Energy", state_e, availability_topic, obj_e, machine_device),
        "energy_wh": 0.0, "stats": [], "mem_attrs": None,
        "stall": {"link": None, "slowdown": None, "slowdown_changes": 0, "last_slowdown": None},
    }
    extra = g.get("extra") or []
    for i, s in enumerate(stats):
        if i >= len(extra) or extra[i].startswith("["):
            continue
        obj, state, config = topics(s[1])
        disc = make_sensor_discovery(
            s[2], state, availability_topic, obj, device, s[3], s[4], "measurement" if s[3] else None)
        if s[0] == "memory.used":
            attrs = f"{prefix}/sensor/{obj}/attributes"
            disc["json_attributes_topic"] = attrs
            entry["mem_attrs"] = attrs
        entry["stats"].append((i, state, config, disc))
    stall = entry["stall"]
    if stall_field(extra, stats, "pcie.link.gen.gpucurrent") is not None and stall_field(extra, stats, "pcie.link.width.current") is not None:
        stall["link"] = _stall_entity(prefix, availability_topic, g, device, "pcie_link", "PCIe Link", None, None, None)
    if any(stall_field(extra, stats, name) is not None for name in SLOWDOWN_FIELDS):
        stall["slowdown"] = _stall_entity(prefix, availability_topic, g, device, "throttle_slowdown", "Throttle Slowdown", None, None, "measurement")
    return entry

def gpu_discovery(g):
    """(config topic, discovery dict) for every entity of one GPU entry."""
    yield g["config_p"], g["disc_p"]
    yield g["config_e"], g["disc_e"]
    for _, _, config, disc in g["stats"]:
        yield config, disc
    for key in ("link", "slowdown"):
        ent = g["stall"][key]
        if ent:
            yield ent["config"], ent["disc"]

def publish_gpu_extras(client, g, extra, stats):
    """Publish VRAM near_full and the stall sensors for one sample. Regular stat states are published by the caller."""
    if g["mem_attrs"]:
        i = next((j for j, s in enumerate(stats) if s[0] == "memory.used"), None)
        if i is not None and i < len(extra) and not extra[i].startswith("["):
            pct = float(extra[i]) / float(extra[mem_total_index(stats)]) * 100
            client.publish(g["mem_attrs"], json.dumps({"near_full": int(pct >= VRAM_NEAR_FULL)}))
    stall = g["stall"]
    gen = stall_field(extra, stats, "pcie.link.gen.gpucurrent")
    width = stall_field(extra, stats, "pcie.link.width.current")
    if stall["link"] and gen is not None and width is not None:
        client.publish(stall["link"]["state"], f"{gen}x{width}")
        client.publish(stall["link"]["attrs"], json.dumps({"gen": gen, "width": width}))
    if stall["slowdown"]:
        flags = {name.split(".")[-1]: int(_reason_active(stall_field(extra, stats, name)))
                 for name in SLOWDOWN_FIELDS if stall_field(extra, stats, name) is not None}
        slow = int(any(flags.values()))
        if stall["last_slowdown"] is not None and slow != stall["last_slowdown"]:
            stall["slowdown_changes"] += 1
        stall["last_slowdown"] = slow
        client.publish(stall["slowdown"]["state"], str(slow))
        client.publish(stall["slowdown"]["attrs"], json.dumps({"changes": stall["slowdown_changes"], **flags}))
