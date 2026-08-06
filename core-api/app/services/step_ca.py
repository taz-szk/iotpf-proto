import subprocess
import tempfile
import os
from app.config import settings

def issue_device_cert(cn: str) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "device.crt")
        key_path = os.path.join(tmpdir, "device.key")

        result = subprocess.run(
            [
                "step", "ca", "certificate", cn,
                cert_path, key_path,
                "--ca-url", settings.step_ca_url,
                "--root", settings.step_ca_root,
                "--provisioner", settings.step_ca_provisioner,
                "--provisioner-password-file", settings.step_ca_password_file,
                "--not-after", "8760h",
                "--san", cn,
                "--force",
                "--no-password",
                "--insecure",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Step-CA certificate issuance failed: {result.stderr}")

        with open(cert_path) as f:
            cert_pem = f.read()
        with open(key_path) as f:
            key_pem = f.read()

    return cert_pem, key_pem
