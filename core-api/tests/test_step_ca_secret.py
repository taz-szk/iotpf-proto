import os
from unittest.mock import MagicMock, patch

import pytest

from app.services import step_ca


def _run_and_capture(password: str, password_file: str = "/nonexistent/password"):
    """_run_stepを実行し、step CLIに渡された--provisioner-password-fileのパスと、その時点の中身を返す。"""
    seen = {}

    def fake_run(cmd, **kwargs):
        pw_path = cmd[cmd.index("--provisioner-password-file") + 1]
        seen["path"] = pw_path
        try:
            with open(pw_path) as f:
                seen["content"] = f.read()
            seen["mode"] = os.stat(pw_path).st_mode & 0o777
        except OSError:
            seen["content"] = None
        # 証明書・鍵の出力先を作っておく
        open(cmd[4], "w").write("CERT")
        open(cmd[5], "w").write("KEY")
        return MagicMock(returncode=0, stderr="")

    with patch.object(step_ca.settings, "step_ca_password", password), \
         patch.object(step_ca.settings, "step_ca_password_file", password_file), \
         patch("app.services.step_ca.subprocess.run", side_effect=fake_run):
        result = step_ca._run_step("t:d", "/certs/ca/root_ca.crt")
    return seen, result


def test_provisioner_password_from_env_is_passed_via_private_temp_file():
    seen, result = _run_and_capture("s3cret-provisioner-password")
    assert seen["content"] == "s3cret-provisioner-password"
    assert seen["path"] != "/nonexistent/password"
    if os.name != "nt":
        assert seen["mode"] == 0o600
    assert result == ("CERT", "KEY")
    # 実行後は一時ファイルごと消えている
    assert not os.path.exists(seen["path"])


def test_falls_back_to_password_file_when_env_not_set():
    seen, _ = _run_and_capture("", password_file="/home/step/secrets/password")
    assert seen["path"] == "/home/step/secrets/password"


def test_ca_root_comes_from_static_mount_not_step_ca_volume(monkeypatch):
    # step-ca のボリューム(CA秘密鍵を含む)はcore-apiに渡さない。ルート証明書は静的マウントを使う。
    captured = {}

    def fake_run_step(cn, ca_root):
        captured["root"] = ca_root
        return "C", "K"

    monkeypatch.setattr(step_ca, "_run_step", fake_run_step)
    monkeypatch.setattr(step_ca.settings, "step_ca_root", "/certs/ca/root_ca.crt")
    step_ca.issue_device_cert("t:d")
    assert captured["root"] == "/certs/ca/root_ca.crt"
