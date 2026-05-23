"""
Padel Replay - Local MQTT Trigger Service
Listens for Zigbee button presses and calls Lambda API
Runs on court machine only (~50MB RAM)
"""

import json
import os
import time
import requests
import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from pathlib import Path

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env")

# Config
MQTT_URL = os.getenv("MQTT_URL", "mqtt://localhost:1883").replace("mqtt://", "")
BUTTON_TOPIC = os.getenv("BUTTON_TOPIC", "zigbee2mqtt/padel_button")
LAMBDA_URL = os.getenv("LAMBDA_URL", "http://localhost:8000")
LAMBDA_AUTH_TOKEN = os.getenv("LAMBDA_AUTH_TOKEN", "dev-token")

MQTT_PARTS = MQTT_URL.split(":")
MQTT_HOST = MQTT_PARTS[0]
MQTT_PORT = int(MQTT_PARTS[1]) if len(MQTT_PARTS) > 1 else 1883

# State
_last_trigger = 0.0
_debounce_seconds = 2.0


def trigger_lambda(action="button"):
    """Call Lambda /trigger endpoint"""
    global _last_trigger

    now = time.time()
    if now - _last_trigger < _debounce_seconds:
        print(f"[debounce] Ignoring trigger (last: {now - _last_trigger:.1f}s ago)")
        return

    _last_trigger = now

    try:
        print(f"[mqtt] Button pressed: {action}")
        response = requests.post(
            f"{LAMBDA_URL}/trigger",
            headers={"Authorization": f"Bearer {LAMBDA_AUTH_TOKEN}"},
            json={"action": action},
            timeout=5,
        )

        if response.status_code == 200:
            data = response.json()
            print(f"[lambda] Clip created: {data.get('clip_id')}")
            print(f"[lambda] S3 URL: {data.get('s3_url')}")
        else:
            print(f"[lambda] Error: {response.status_code} - {response.text}")

    except requests.exceptions.Timeout:
        print("[lambda] Timeout - Lambda may be encoding previous clip")
    except Exception as e:
        print(f"[lambda] Error: {e}")


def on_connect(client, userdata, flags, rc):
    """MQTT connect callback"""
    if rc == 0:
        print(f"[mqtt] Connected to {MQTT_HOST}:{MQTT_PORT}")
        client.subscribe(BUTTON_TOPIC)
        print(f"[mqtt] Subscribed to: {BUTTON_TOPIC}")
    else:
        print(f"[mqtt] Connection failed: rc={rc}")


def on_message(client, userdata, msg):
    """MQTT message callback"""
    try:
        payload = json.loads(msg.payload.decode())
        action = str(payload.get("action", "")).lower()

        # Detect button actions
        if any(x in action for x in ["single", "double", "long", "hold"]):
            trigger_lambda(action)
        else:
            print(f"[mqtt] Ignoring action: {action}")

    except json.JSONDecodeError:
        print(f"[mqtt] Invalid JSON: {msg.payload}")
    except Exception as e:
        print(f"[mqtt] Error processing message: {e}")


def main():
    """Start MQTT listener"""
    print("\n" + "=" * 60)
    print("PADEL REPLAY - LOCAL MQTT TRIGGER")
    print("=" * 60)
    print(f"MQTT Broker: {MQTT_HOST}:{MQTT_PORT}")
    print(f"Button Topic: {BUTTON_TOPIC}")
    print(f"Lambda URL: {LAMBDA_URL}")
    print(f"Debounce: {_debounce_seconds}s")
    print("=" * 60 + "\n")

    # Create MQTT client
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
        print("[mqtt] Listening for button presses... (Ctrl+C to stop)\n")

        # Keep running
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[mqtt] Shutting down...")
    except Exception as e:
        print(f"[mqtt] Fatal error: {e}")
    finally:
        client.loop_stop()
        client.disconnect()
        print("[mqtt] Disconnected")


if __name__ == "__main__":
    main()
