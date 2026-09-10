import os
import requests


def provision(api_url: str, bootstrap_token: str, device_id: str, cert_dir: str, verify: bool = True,
              group_id: str = None) -> tuple:
    os.makedirs(cert_dir, exist_ok=True)
    if not verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    body = {"bootstrap_token": bootstrap_token, "device_id": device_id}
    if group_id:
        body["group_id"] = group_id
    resp = requests.post(
        f"{api_url}/provision",
        json=body,
        timeout=30,
        verify=verify,
    )
    resp.raise_for_status()
    data = resp.json()

    _write(cert_dir, "cert.pem", data["certificate"])
    _write(cert_dir, "key.pem", data["private_key"])
    _write(cert_dir, "ca.pem", data["ca_certificate"])
    _write(cert_dir, "tenant_id", data["tenant_id"])
    _write(cert_dir, "device_id", data["device_id"])

    return data["tenant_id"], data["device_id"]


def list_groups(api_url: str, bootstrap_token: str, verify: bool = True) -> list:
    if not verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    resp = requests.post(
        f"{api_url}/provision/groups",
        json={"bootstrap_token": bootstrap_token},
        timeout=30,
        verify=verify,
    )
    resp.raise_for_status()
    return resp.json()


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
