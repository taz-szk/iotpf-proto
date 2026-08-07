"""OTA コマンド受信 + ファームウェアダウンロード・検証"""
import os
import sys
from iot_platform import IotClient, OtaHandler

API_URL = os.environ.get("IOT_API_URL", "https://localhost/api")
BROKER_HOST = os.environ.get("MQTT_BROKER_HOST", "localhost")
CERT_DIR = os.environ.get("CERT_DIR", "./certs")
FIRMWARE_OUTPUT = os.environ.get("FIRMWARE_OUTPUT", "/tmp/firmware.bin")


def on_command(cmd_type: str, payload: dict) -> None:
    if cmd_type != "ota":
        print(f"Unknown command type: {cmd_type}")
        return

    print(f"OTA command received: version={payload.get('version')}")
    success = OtaHandler.handle(payload, FIRMWARE_OUTPUT)
    if success:
        print(f"Firmware downloaded and verified: {FIRMWARE_OUTPUT}")
        print("Apply firmware here (platform-specific)")
    else:
        print("ERROR: Checksum mismatch — firmware rejected", file=sys.stderr)


client = IotClient(API_URL, broker_host=BROKER_HOST, broker_port=8883)
client.load_credentials(CERT_DIR)
client.on_command(on_command)
client.connect()
print("Waiting for OTA commands. Press Ctrl+C to stop.")

try:
    client.loop_forever()
except KeyboardInterrupt:
    client.disconnect()
    print("Disconnected.")
