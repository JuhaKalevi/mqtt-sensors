#!/usr/bin/env python3
"""One-off: list retained MQTT messages and optionally clear them with empty retained payloads; --entity also removes a (possibly non-retained) HA entity."""
import argparse, time
from mqtt_common import get_hostname, get_mqtt_settings, create_client, clear_retained

parser = argparse.ArgumentParser(description="List retained MQTT topics (dry run) and clear them with --delete.")
parser.add_argument("--hostname", default=get_hostname(), help="only topics containing this name (default: this machine)")
parser.add_argument("--all", action="store_true", help="every retained topic on the broker, not just this hostname")
parser.add_argument("--entity", action="append", default=[], metavar="COMPONENT/OBJECT", help="only this entity (repeatable, e.g. sensor/pearl_network_hashrate): its retained topics plus its config topic, so --delete removes it from HA even when its discovery was not retained")
parser.add_argument("--delete", action="store_true", help="publish zero-length retained payloads to clear the listed topics")
parser.add_argument("--wait", type=float, default=3.0, help="seconds to collect retained messages")
args = parser.parse_args()

settings = get_mqtt_settings()
needle = args.hostname.lower()
entities = {f"{settings['prefix']}/{e}" for e in args.entity}
retained = {}

def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code == 0:
        client.subscribe("#")

def on_message(client, userdata, msg):
    if msg.retain and msg.payload and (msg.topic.rpartition("/")[0] in entities if entities else args.all or needle in msg.topic.lower()):
        retained[msg.topic] = msg.payload

client = create_client(f"mqtt-purge-{get_hostname()}", settings)
client.message_callback_remove(f"{settings['prefix']}/status")
client.on_connect = on_connect
client.on_message = on_message
client.connect(settings["host"], settings["port"], keepalive=60)
client.loop_start()
time.sleep(args.wait)

removed = sorted({f"{e}/config" for e in entities} - set(retained))
for topic in sorted(retained):
    print(f"{topic}  ({len(retained[topic])} bytes)")
for topic in removed:
    print(f"{topic}  (entity config, not retained)")
print(f"{len(retained)} retained topic(s) {'for ' + ', '.join(args.entity) if entities else 'on the broker' if args.all else 'for ' + args.hostname}")

if args.delete:
    clear_retained(client, sorted(retained) + removed)
    print(f"cleared {len(retained) + len(removed)} topic(s)")
elif retained or removed:
    print("dry run; add --delete to clear them")

client.loop_stop()
client.disconnect()
