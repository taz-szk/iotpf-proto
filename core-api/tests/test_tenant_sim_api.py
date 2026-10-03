"""SIM管理APIのテスト。認証情報はadmin限定、回線操作はadmin/operatorで権限分けする。
認証情報(Auth Key)はレスポンス・エラーに含めない。"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import create_access_token
from app.services.sim_service import SoracomNotConfiguredError
from app.services.soracom_client import SoracomApiError

client = TestClient(app)

TENANT_ID = "44444444-4444-4444-4444-444444444444"


def _cookies(role="operator"):
    t = create_access_token({"sub": "user-id", "email": "u@test.com", "type": "tenant",
                            "tenant_id": TENANT_ID, "role": role})
    return {"iot_token": t}


def test_get_credentials_summary():
    summary = {"configured": True, "coverage": "jp", "auth_key_id_hint": "...1234"}
    with patch("app.routers.tenant_sim.get_credentials_summary", return_value=summary):
        resp = client.get("/tenant-portal/me/sim/credentials", cookies=_cookies())
    assert resp.status_code == 200
    assert resp.json() == summary


def test_put_credentials_requires_admin():
    resp = client.put("/tenant-portal/me/sim/credentials", cookies=_cookies("operator"),
                      json={"coverage": "jp", "auth_key_id": "keyId-x", "auth_key": "secret-x"})
    assert resp.status_code == 403


def test_put_credentials_validates_before_saving():
    with patch("app.routers.tenant_sim.verify_credentials", return_value=False) as mock_verify, \
         patch("app.routers.tenant_sim.save_credentials") as mock_save:
        resp = client.put("/tenant-portal/me/sim/credentials", cookies=_cookies("admin"),
                          json={"coverage": "jp", "auth_key_id": "keyId-x", "auth_key": "secret-x"})
    assert resp.status_code == 422
    mock_verify.assert_called_once()
    mock_save.assert_not_called()


def test_put_credentials_saves_when_valid():
    with patch("app.routers.tenant_sim.verify_credentials", return_value=True), \
         patch("app.routers.tenant_sim.save_credentials") as mock_save, \
         patch("app.routers.tenant_sim.log_audit") as mock_audit:
        resp = client.put("/tenant-portal/me/sim/credentials", cookies=_cookies("admin"),
                          json={"coverage": "jp", "auth_key_id": "keyId-x", "auth_key": "secret-x"})
    assert resp.status_code == 204
    mock_save.assert_called_once_with(TENANT_ID, "jp", "keyId-x", "secret-x")
    assert mock_audit.call_args.args[3] == "save_soracom_credentials"


def test_put_credentials_coverage_only_update_skips_verification():
    with patch("app.routers.tenant_sim.verify_credentials") as mock_verify, \
         patch("app.routers.tenant_sim.save_credentials") as mock_save:
        resp = client.put("/tenant-portal/me/sim/credentials", cookies=_cookies("admin"), json={"coverage": "g"})
    assert resp.status_code == 204
    mock_verify.assert_not_called()
    mock_save.assert_called_once_with(TENANT_ID, "g", None, None)


def test_delete_credentials_requires_admin():
    resp = client.delete("/tenant-portal/me/sim/credentials", cookies=_cookies("operator"))
    assert resp.status_code == 403


def test_delete_credentials_as_admin():
    with patch("app.routers.tenant_sim.delete_credentials") as mock_delete, \
         patch("app.routers.tenant_sim.log_audit"):
        resp = client.delete("/tenant-portal/me/sim/credentials", cookies=_cookies("admin"))
    assert resp.status_code == 204
    mock_delete.assert_called_once_with(TENANT_ID)


def test_list_lines_passes_filters_and_returns_the_result():
    result = {"items": [{"imsi": "440100000001", "bound_device_id": None}], "next_cursor": "next-key"}
    with patch("app.routers.tenant_sim.list_lines", return_value=result) as mock_list:
        resp = client.get(
            "/tenant-portal/me/sim/lines?status=active&speed_class=s1.standard&bound=false&cursor=prev&limit=5",
            cookies=_cookies(),
        )
    assert resp.status_code == 200
    assert resp.json() == result
    mock_list.assert_called_once_with(
        TENANT_ID, status="active", speed_class="s1.standard", tag_name=None, tag_value=None,
        bound=False, cursor="prev", limit=5,
    )


def test_list_lines_without_credentials_returns_400():
    with patch("app.routers.tenant_sim.list_lines", side_effect=SoracomNotConfiguredError(TENANT_ID)):
        resp = client.get("/tenant-portal/me/sim/lines", cookies=_cookies())
    assert resp.status_code == 400


def test_get_line_not_found_returns_404():
    with patch("app.routers.tenant_sim.get_line", return_value=None):
        resp = client.get("/tenant-portal/me/sim/lines/000000", cookies=_cookies())
    assert resp.status_code == 404


def test_activate_requires_operator_or_admin():
    resp = client.post("/tenant-portal/me/sim/lines/440100000001/activate", cookies=_cookies("viewer"))
    assert resp.status_code == 403


def test_activate_logs_the_audit_entry_without_the_imsi_leaking_credentials():
    with patch("app.routers.tenant_sim.activate_line", return_value={"imsi": "440100000001", "status": "active"}), \
         patch("app.routers.tenant_sim.log_audit") as mock_audit:
        resp = client.post("/tenant-portal/me/sim/lines/440100000001/activate", cookies=_cookies("operator"))
    assert resp.status_code == 200
    assert mock_audit.call_args.args[3] == "activate_sim"
    assert mock_audit.call_args.kwargs["resource_id"] == "440100000001"


def test_deactivate_not_found_returns_404():
    with patch("app.routers.tenant_sim.deactivate_line", return_value=None):
        resp = client.post("/tenant-portal/me/sim/lines/000000/deactivate", cookies=_cookies("operator"))
    assert resp.status_code == 404


def test_speed_class_update():
    with patch("app.routers.tenant_sim.set_speed_class", return_value={"speedClass": "s1.fast"}) as mock_set, \
         patch("app.routers.tenant_sim.log_audit"):
        resp = client.post("/tenant-portal/me/sim/lines/440100000001/speed-class",
                           cookies=_cookies("operator"), json={"speed_class": "s1.fast"})
    assert resp.status_code == 200
    mock_set.assert_called_once_with(TENANT_ID, "440100000001", "s1.fast")


def test_list_lines_soracom_failure_returns_502():
    with patch("app.routers.tenant_sim.list_lines", side_effect=SoracomApiError("SORACOM側で一時的な問題が発生しています")):
        resp = client.get("/tenant-portal/me/sim/lines", cookies=_cookies())
    assert resp.status_code == 502
    assert "一時的な問題" in resp.json()["detail"]
