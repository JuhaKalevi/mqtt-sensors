#!/usr/bin/env python3
"""List retained MQTT messages and optionally clear them with empty retained payloads; --entity and --script delete entities with force_purge; --inventory attributes object ids to the collectors."""
import argparse, json, os, subprocess, sys, time
from pathlib import Path
from lib import mqtt_common
from lib.mqtt_common import (
    get_hostname, get_machine_id, get_mqtt_settings, create_client, clear_retained, force_purge
)

parser = argparse.ArgumentParser(description="List retained MQTT topics (dry run) and clear them with --delete.")
parser.add_argument("--hostname", help="only topics containing this name (default: this machine's hostname or MACHINE_ID; hardware-keyed ids such as mb_/gpu_/peripheral batteries contain neither)")
parser.add_argument("--all", action="store_true", help="every retained topic on the broker, not just this hostname")
parser.add_argument("--entity", action="append", default=[], metavar="COMPONENT/OBJECT", help="only this entity (repeatable, e.g. sensor/Olohuone_PC1_input_apm_total): its retained topics plus its config topic, so --delete removes it from HA even when its discovery was not retained")
parser.add_argument("--script", metavar="PATH", help="every entity and retained topic of a collector (e.g. support/pearl_network.py): runs it with --list, or --purge with --delete (stop the collector first), or --purge-stale with --delete --stale")
parser.add_argument("--stale", action="store_true", help="with --script and --delete: run it with --purge-stale instead of --purge (only stale topics; current ids untouched, safe while the collector runs)")
parser.add_argument("--only", choices=("current", "stale"), help="with --script (no --delete): run it with --list --only (current or stale topics)")
parser.add_argument("--inventory", action="store_true", help="read-only: every object id seen under <prefix>/+/+/+ (retained, or live during --wait), grouped per object and attributed to the collectors on this machine (--claim); objects no collector claims are listed as UNCLAIMED. Never deletes")
parser.add_argument("--host", metavar="TEXT", help="with --inventory: only objects whose id contains TEXT (case-insensitive), e.g. a hostname or MACHINE_ID")
parser.add_argument("--delete", action="store_true", help="publish zero-length retained payloads to clear the listed topics")
parser.add_argument("--wait", type=float, default=3.0, help="seconds to collect retained messages")
args = parser.parse_args()
if args.only and (not args.script or args.delete):
    parser.error("--only needs --script and no --delete")
if args.inventory and (args.delete or args.script or args.entity or args.all):
    parser.error("--inventory is read-only and takes only --host and --wait")

REPO = Path(__file__).resolve().parent

def collectors():
    """Collector scripts for this OS: repo root (Linux) or windows/ (Windows), plus support/."""
    skip = {"mqtt_purge_retained.py", "tray.py"}
    host = REPO / "windows" if os.name == "nt" else REPO
    return sorted(p for d in (host, REPO / "support") for p in d.glob("*.py") if p.name not in skip and not p.name.startswith("ha_"))

def inventory(settings):
    prefix = settings["prefix"]
    seen = {}
    client = mqtt_common.mqtt.Client(mqtt_common.mqtt.CallbackAPIVersion.VERSION2, client_id=f"mqtt-inventory-{get_hostname()}")
    if settings["user"]:
        client.username_pw_set(settings["user"], settings["password"])
    def on_connect(c, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            c.subscribe(f"{prefix}/+/+/+")
    def on_message(c, userdata, msg):
        component, obj, leaf = msg.topic.split("/")[-3:]
        if msg.payload and (not args.host or args.host.lower() in obj.lower()):
            seen.setdefault(obj, {}).setdefault(f"{component}/{leaf}", "retained" if msg.retain else "live")
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(settings["host"], settings["port"], keepalive=60)
    client.loop_start()
    time.sleep(args.wait)
    client.loop_stop()
    client.disconnect()
    claims, skipped = {}, []
    for script in collectors():
        name = script.relative_to(REPO).as_posix()
        proc = subprocess.run([sys.executable, str(script), "--claim"], input="\n".join(sorted(seen)), capture_output=True, text=True, cwd=REPO, timeout=120)
        try:
            found = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 else None
        except (IndexError, ValueError):
            found = None
        if found is None:
            skipped.append(f"{name}: {(proc.stderr.strip().splitlines() or ['exit ' + str(proc.returncode)])[-1]}")
            continue
        for obj, kind in found.items():
            claims.setdefault(obj, []).append((kind, name))
    groups = {"current": [], "scope": [], "UNCLAIMED": []}
    for obj in sorted(seen):
        kinds = claims.get(obj, [])
        best = next((k for k in ("current", "scope") if any(c[0] == k for c in kinds)), "UNCLAIMED")
        leaves = ", ".join(f"{t}{' (retained)' if r == 'retained' else ''}" for t, r in sorted(seen[obj].items()))
        groups[best].append(f"  {obj}  [{leaves}]" + (f"  <- {', '.join(f'{n} ({k})' for k, n in kinds)}" if kinds else ""))
    titles = {"current": "current ids (published now by a collector here)",
              "scope": "in a collector's scope but not current (stale; --purge-stale removes them)", "UNCLAIMED": "UNCLAIMED (no collector on this machine owns them: other machines, other integrations, or leftovers for --entity)"}
    for key, lines in groups.items():
        print(f"{titles[key]}: {len(lines)}")
        print("\n".join(lines) if lines else "  -")
    for s in skipped:
        print(f"skipped {s}")
    print(f"inventory: {len(seen)} object(s){' containing ' + repr(args.host) if args.host else ''}; read-only, nothing deleted", flush=True)

if args.inventory:
    inventory(get_mqtt_settings())
    sys.exit(0)

if args.script:
    flags = [("--purge-stale" if args.stale else "--purge")] if args.delete else ["--list"] + (["--only", args.only] if args.only else [])
    sys.exit(subprocess.run([sys.executable, str(Path(args.script).resolve()), *flags], cwd=Path(__file__).resolve().parent).returncode)

settings = get_mqtt_settings()

if args.entity:
    force_purge(f"mqtt-purge-{get_hostname()}", settings, entities=args.entity, apply=args.delete)
    sys.exit(0)

needles = {args.hostname.lower()} if args.hostname else {get_hostname().lower(), get_machine_id().lower()}
retained = {}

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe("#")

def on_message(client, userdata, msg):
    if msg.retain and msg.payload and (args.all or any(n in msg.topic.lower() for n in needles)):
        retained[msg.topic] = msg.payload

client = create_client(f"mqtt-purge-{get_hostname()}", settings)
client.message_callback_remove(f"{settings['prefix']}/status")
client.on_connect = on_connect
client.on_message = on_message
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()
time.sleep(args.wait)

for topic in sorted(retained):
    print(f"{topic}  ({len(retained[topic])} bytes)")
print(f"{len(retained)} retained topic(s) {'on the broker' if args.all else 'for ' + ' or '.join(sorted(needles))}")

if args.delete:
    clear_retained(client, sorted(retained))
    print(f"cleared {len(retained)} topic(s)")
elif retained:
    print("dry run; add --delete to clear them")

client.loop_stop()
client.disconnect()
