"""テレメトリ送信ループ: 5秒ごとにダミーセンサー値を送信する"""
import os
import time
import random
from iot_platform import IotClient

API_URL = os.environ.get("IOT_API_URL", "https://localhost/api")
BROKER_HOST = os.environ.get("MQTT_BROKER_HOST", "localhost")
CERT_DIR = os.environ.get("CERT_DIR", "./certs")

client = IotClient(API_URL, broker_host=BROKER_HOST, broker_port=8883)
client.load_credentials(CERT_DIR)
client.connect()
print("Connected. Publishing telemetry every 5s. Press Ctrl+C to stop.")

try:
    while True:
        client.publish_telemetry({
            "temperature": round(random.uniform(20.0, 30.0), 2),
            "humidity": round(random.uniform(40.0, 80.0), 2),
            "pressure": round(random.uniform(1000.0, 1020.0), 2),
        })
        print(".", end="", flush=True)
        time.sleep(5)
except KeyboardInterrupt:
    pass
finally:
    client.disconnect()
    print("\nDisconnected.")
