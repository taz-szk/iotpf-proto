"""初回プロビジョニング: ブートストラップトークンでデバイス証明書を取得する"""
import os
import sys
from iot_platform import IotClient

API_URL = os.environ.get("IOT_API_URL", "https://localhost/api")
BOOTSTRAP_TOKEN = os.environ["BOOTSTRAP_TOKEN"]
DEVICE_ID = os.environ.get("DEVICE_ID", "my-sensor-001")
CERT_DIR = os.environ.get("CERT_DIR", "./certs")

client = IotClient(API_URL, broker_host="")  # broker_host は接続時に必要
client.provision(BOOTSTRAP_TOKEN, DEVICE_ID, CERT_DIR)
print(f"Provisioned device '{DEVICE_ID}' — certs saved to {CERT_DIR}")
