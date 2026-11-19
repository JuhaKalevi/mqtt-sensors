"""NVIDIA GPU ids and devices shared by nvidia_gpu_power.py and windows/nvidia_gpu_power.py.

Power and energy are slot-keyed on the machine device (<slot>_gpu<N>_power / _energy, generic names), so the history
of a slot spans GPU swaps for power tracking. The health stats follow the card: keyed by its UUID plus a short model
name for readability (gpu_<uuid>_<Model>_<metric>, e.g. gpu_8b1c…_RTX4080_temperature), on its own HA device linked
to the machine.
"""
import re
from lib.mqtt_common import (
    id_slug, slot_id, make_linked_device, make_power_discovery, make_energy_discovery, make_sensor_discovery
)

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

def gpu_lists(gpus, metrics):
    """Entities of nvidia_gpu_power for [(index, uuid, name)]: slot-keyed power/energy, card-keyed stats."""
    entities = []
    for idx, uuid, name in gpus:
        entities += [f"sensor/{slot_id(f'gpu{idx}_{m}')}" for m in ("power", "energy")]
        entities += [f"sensor/{gpu_stat_id(uuid, name, m)}" for m in metrics]
    return tuple(entities)

def gpu_entities(prefix, availability_topic, g, stats, machine_device, multi):
    """Topics and discovery of one GPU sample row g (idx, name, uuid, extra): power and energy on the machine device
    (generic slot ids, named by the current card's model: "GeForce RTX 4080 Power", "GeForce RTX 4080 (GPU <N>) Power"
    when the machine has several), stats on the GPU's own linked device (named by the same model)."""
    device = make_linked_device(gpu_device_id(g["uuid"]), g["name"], machine_device["identifiers"][0], manufacturer="NVIDIA", model=g["name"])

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
        "energy_wh": 0.0, "stats": [],
    }
    for i, s in enumerate(stats):
        if g["extra"][i].startswith("["):
            continue
        obj, state, config = topics(s[1])
        entry["stats"].append((i, state, config, make_sensor_discovery(
            s[2], state, availability_topic, obj, device, s[3], s[4], "measurement" if s[3] else None)))
    return entry
