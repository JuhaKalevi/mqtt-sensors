#!/usr/bin/env python3
"""One-off: list retained MQTT messages and optionally clear them with empty retained payloads; --entity and --script force-delete entities with force_purge."""
import argparse, subprocess, sys, time
from pathlib import Path
from mqtt_common import get_hostname, get_mqtt_settings, create_client, clear_retained, force_purge

parser = argparse.ArgumentParser(description="List retained MQTT topics (dry run) and clear them with --delete.")
parser.add_argument("--hostname", default=get_hostname(), help="only topics containing this name (default: this machine)")
parser.add_argument("--all", action="store_true", help="every retained topic on the broker, not just this hostname")
parser.add_argument("--entity", action="append", default=[], metavar="COMPONENT/OBJECT", help="only this entity (repeatable, e.g. sensor/pearl_network_hashrate): its retained topics plus its config topic, so --delete removes it from HA even when its discovery was not retained")
parser.add_argument("--script", metavar="PATH", help="every entity and retained topic of a sweep-enabled collector (e.g. support/pearl_network.py): runs it with --list, or --purge with --delete; stop the collector first")
parser.add_argument("--stale", action="store_true", help="with --script and --delete: run it with --purge-stale instead of --purge (scripts that support it, e.g. hwmon_drive_temperature.py)")
parser.add_argument("--delete", action="store_true", help="publish zero-length retained payloads to clear the listed topics")
parser.add_argument("--wait", type=float, default=3.0, help="seconds to collect retained messages")
args = parser.parse_args()

if args.script:
    sys.exit(subprocess.run([sys.executable, str(Path(args.script).resolve()), ("--purge-stale" if args.stale else "--purge") if args.delete else "--list"], cwd=Path(__file__).resolve().parent).returncode)

settings = get_mqtt_settings()

if args.entity:
    force_purge(f"mqtt-purge-{get_hostname()}", settings, entities=args.entity, apply=args.delete)
    sys.exit(0)

needle = args.hostname.lower()
retained = {}

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe("#")

def on_message(client, userdata, msg):
    if msg.retain and msg.payload and (args.all or needle in msg.topic.lower()):
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
print(f"{len(retained)} retained topic(s) {'on the broker' if args.all else 'for ' + args.hostname}")

if args.delete:
    clear_retained(client, sorted(retained))
    print(f"cleared {len(retained)} topic(s)")
elif retained:
    print("dry run; add --delete to clear them")

client.loop_stop()
client.disconnect()
