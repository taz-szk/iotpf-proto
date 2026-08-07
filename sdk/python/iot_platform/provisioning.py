import os
import requests


def provision(api_url: str, bootstrap_token: str, device_id: str, cert_dir: str) -> tuple:
    os.makedirs(cert_dir, exist_ok=True)
    resp = requests.post(
        f"{api_url}/provision",
        json={"bootstrap_token": bootstrap_token, "device_id": device_id},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    _write(cert_dir, "cert.pem", data["certificate"])
    _write(cert_dir, "key.pem", data["private_key"])
    _write(cert_dir, "ca.pem", data["ca_certificate"])
    _write(cert_dir, "tenant_id", data["tenant_id"])
    _write(cert_dir, "device_id", data["device_id"])

    return data["tenant_id"], data["device_id"]


def load_credentials(cert_dir: str) -> tuple:
    tid_path = os.path.join(cert_dir, "tenant_id")
    if not os.path.exists(tid_path):
        raise FileNotFoundError(f"No credentials in {cert_dir}. Run provision() first.")
    with open(tid_path) as f:
        tenant_id = f.read().strip()
    return (
        tenant_id,
        os.path.join(cert_dir, "cert.pem"),
        os.path.join(cert_dir, "key.pem"),
        os.path.join(cert_dir, "ca.pem"),
    )


def _write(cert_dir: str, filename: str, content: str) -> None:
    with open(os.path.join(cert_dir, filename), "w") as f:
        f.write(content)
