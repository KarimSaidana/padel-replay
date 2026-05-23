"""
Padel Replay - Local MQTT Trigger
Listens for Zigbee button presses and calls Lambda to create a clip.
"""

import json
import os
import time
import requests
import paho.mqtt.client as mqtt
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

MQTT_URL       = os.getenv("MQTT_URL", "mqtt://localhost:1883").replace("mqtt://", "")
BUTTON_TOPIC   = os.getenv("BUTTON_TOPIC", "zigbee2mqtt/padel_button")
EC2_URL        = os.getenv("EC2_URL", "http://localhost:5000")
AUTH_TOKEN     = os.getenv("RECORDER_AUTH_TOKEN", "dev-token")

MQTT_PARTS = MQTT_URL.split(":")
MQTT_HOST  = MQTT_PARTS[0]
MQTT_PORT  = int(MQTT_PARTS[1]) if len(MQTT_PARTS) > 1 else 1883

_last_trigger    = 0.0
_debounce_seconds = 2.0


def trigger_lambda(action="button"):
    global _last_trigger
    now = time.time()
    if now - _last_trigger < _debounce_seconds:
        return
    _last_trigger = now

    ts = datetime.now().strftime("%H:%M:%S")
    print(f"\n{'='*50}", flush=True)
    print(f"  BUTTON PRESSED  [{ts}]  action={action}", flush=True)
    print(f"{'='*50}", flush=True)
    print(f"  Calling EC2 recorder... (saving last 30s)", flush=True)

    try:
        response = requests.post(
            f"{EC2_URL}/save",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            json={"action": action},
            timeout=120,
        )

        if response.status_code == 200:
            data = response.json()
            print(f"  Clip created:  {data.get('clip_id')}", flush=True)
            print(f"  Watch it here: {data.get('s3_url')}", flush=True)
            print(f"{'='*50}\n", flush=True)
        elif response.status_code == 503:
            print(f"  ERROR: Buffer empty on EC2.", flush=True)
            print(f"  Is the Stream Relay running and connected?", flush=True)
            print(f"{'='*50}\n", flush=True)
        else:
            print(f"  ERROR: Lambda returned {response.status_code}", flush=True)
            print(f"  {response.text[:300]}", flush=True)
            print(f"{'='*50}\n", flush=True)

    except requests.exceptions.Timeout:
        print(f"  ERROR: Lambda timed out after 120s", flush=True)
        print(f"{'='*50}\n", flush=True)
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)
        print(f"{'='*50}\n", flush=True)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(BUTTON_TOPIC)
        print(f"[mqtt] Connected - listening on: {BUTTON_TOPIC}", flush=True)
        print(f"[mqtt] Waiting for button press...\n", flush=True)
    else:
        print(f"[mqtt] Connection failed (rc={rc}) - retrying...", flush=True)


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode())
        action  = str(payload.get("action", "")).lower()
        if action:
            print(f"[mqtt] Received action: '{action}'", flush=True)
        if any(x in action for x in ["single", "double", "long", "hold", "click", "toggle", "on", "press"]):
            trigger_lambda(action)
    except Exception:
        pass


def main():
    print(f"\n{'='*50}", flush=True)
    print(f"  PADEL REPLAY - MQTT TRIGGER", flush=True)
    print(f"{'='*50}", flush=True)
    print(f"  Broker:  {MQTT_HOST}:{MQTT_PORT}", flush=True)
    print(f"  Topic:   {BUTTON_TOPIC}", flush=True)
    print(f"  EC2:     {EC2_URL}", flush=True)
    print(f"{'='*50}\n", flush=True)

    try:
        from paho.mqtt.enums import CallbackAPIVersion
        client = mqtt.Client(CallbackAPIVersion.VERSION1)
    except (ImportError, AttributeError):
        client = mqtt.Client()

    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect_async(MQTT_HOST, MQTT_PORT)
        client.loop_start()
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[mqtt] Stopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
