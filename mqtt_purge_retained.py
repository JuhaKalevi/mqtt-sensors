#!/usr/bin/env python3
"""One-off: list retained MQTT messages and optionally clear them with empty retained payloads."""
import argparse, time
from mqtt_common import get_hostname, get_mqtt_settings, create_client

parser = argparse.ArgumentParser(description="List retained MQTT topics (dry run) and clear them with --delete.")
parser.add_argument("--hostname", default=get_hostname(), help="only topics containing this name (default: this machine)")
parser.add_argument("--all", action="store_true", help="every retained topic on the broker, not just this hostname")
parser.add_argument("--delete", action="store_true", help="publish zero-length retained payloads to clear the listed topics")
parser.add_argument("--wait", type=float, default=3.0, help="seconds to collect retained messages")
args = parser.parse_args()

settings = get_mqtt_settings()
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
print(f"{len(retained)} retained topic(s) {'for ' + args.hostname if not args.all else 'on the broker'}")

if args.delete:
    for topic in sorted(retained):
        client.publish(topic, b"", qos=1, retain=True).wait_for_publish()  # retained: clear stored message
    print(f"cleared {len(retained)} topic(s)")
elif retained:
    print("dry run; add --delete to clear them")

client.loop_stop()
client.disconnect()
