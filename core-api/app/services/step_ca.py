import logging
import os
import subprocess
import tempfile
import time

from app.config import settings

logger = logging.getLogger(__name__)

# step-ca ボリューム内 CA を優先（静的マウントと食い違いを防ぐ）
_STEP_CA_VOL_ROOT = "/home/step/certs/root_ca.crt"


def issue_device_cert(cn: str) -> tuple[str, str]:
    ca_root = _STEP_CA_VOL_ROOT if os.path.exists(_STEP_CA_VOL_ROOT) else settings.step_ca_root

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            cert_pem, key_pem = _run_step(cn, ca_root)
            return cert_pem, key_pem
        except RuntimeError as exc:
            last_error = exc
            logger.warning("step ca certificate attempt %d/3 failed for %s: %s", attempt, cn, exc)
            if attempt < 3:
                time.sleep(2 * attempt)

    raise last_error  # type: ignore[misc]


def _run_step(cn: str, ca_root: str) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as tmpdir:
        cert_path = os.path.join(tmpdir, "device.crt")
        key_path = os.path.join(tmpdir, "device.key")

        # 空パスワードファイルを使ってキーを平文PEMで出力（--no-password は 0.24+ で廃止）
        empty_pass = os.path.join(tmpdir, "empty.txt")
        open(empty_pass, "w").close()

        result = subprocess.run(
            [
                "step", "ca", "certificate", cn,
                cert_path, key_path,
                "--ca-url", settings.step_ca_url,
                "--root", ca_root,
                "--provisioner", settings.step_ca_provisioner,
                "--provisioner-password-file", settings.step_ca_password_file,
                "--not-after", "8760h",
                "--san", cn,
                "--force",
                "--password-file", empty_pass,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())

        with open(cert_path) as f:
            cert_pem = f.read()
        with open(key_path) as f:
            key_pem = f.read()

    return cert_pem, key_pem
