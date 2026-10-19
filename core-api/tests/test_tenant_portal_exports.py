from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token
from app.services.export_jobs import JobAlreadyRunningError, ExportTooLargeError

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "22222222-2222-2222-2222-222222222222"


def _admin_token():
    return create_access_token({"sub": "u1", "email": "a@test.com", "type": "tenant",
                                 "tenant_id": TENANT_ID, "role": "admin"})


def _token(role: str, tenant_id: str = TENANT_ID):
    return create_access_token({"sub": "u1", "email": "a@test.com", "type": "tenant",
                                 "tenant_id": tenant_id, "role": role})


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_request_export_rejects_when_already_running():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.request_adhoc_export", side_effect=JobAlreadyRunningError()):
        mock_session.return_value = _session_ctx()
        resp = client.post("/tenant-portal/me/exports",
                            json={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                                  "device_ids": None, "group_id": None},
                            cookies={"iot_token": _admin_token()})
    assert resp.status_code == 409


def test_request_export_rejects_when_too_large():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.request_adhoc_export",
               side_effect=ExportTooLargeError(99_999_999, 20_000_000)):
        mock_session.return_value = _session_ctx()
        resp = client.post("/tenant-portal/me/exports",
                            json={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                                  "device_ids": None, "group_id": None},
                            cookies={"iot_token": _admin_token()})
    assert resp.status_code == 422


def test_download_export_returns_404_when_not_done():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = None
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/exports/adhoc/download",
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 404


def test_download_export_returns_404_for_different_tenant():
    # 別テナント(OTHER_TENANT_ID)のジョブがmockのquery/filterの実装不備等で返ってきても、
    # 呼び出し元テナントと一致しなければダウンロードできないことを確認する(クロステナント対策)。
    job = MagicMock(status="done", minio_key="some-key", tenant_id=OTHER_TENANT_ID)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = job
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/exports/adhoc/download",
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 404


def test_request_export_rejects_non_admin_role():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.request_adhoc_export") as mock_request:
        mock_session.return_value = _session_ctx()
        resp = client.post("/tenant-portal/me/exports",
                            json={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                                  "device_ids": None, "group_id": None},
                            cookies={"iot_token": _token("viewer")})
    assert resp.status_code == 403
    mock_request.assert_not_called()


def test_delete_export_rejects_non_admin_role():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.delete_export_file") as mock_delete:
        mock_session.return_value = _session_ctx()
        resp = client.delete("/tenant-portal/me/exports/adhoc",
                              cookies={"iot_token": _token("viewer")})
    assert resp.status_code == 403
    mock_delete.assert_not_called()


def test_download_export_allows_non_admin_role():
    job = MagicMock(status="done", minio_key="some-key", tenant_id=TENANT_ID)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.stream_export_file", return_value=iter([b"a,b\n1,2\n"])):
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = job
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/exports/adhoc/download",
                           cookies={"iot_token": _token("viewer")})
    assert resp.status_code == 200


def _post_export(json_body, mock_request=None):
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.request_adhoc_export", mock_request or MagicMock()) as m:
        mock_session.return_value = _session_ctx()
        resp = client.post("/tenant-portal/me/exports", json=json_body,
                            cookies={"iot_token": _admin_token()})
    return resp, m


def test_request_export_returns_502_with_message_when_influx_unavailable():
    from app.services.export_csv import InfluxUnavailableError
    resp, _ = _post_export(
        {"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"},
        MagicMock(side_effect=InfluxUnavailableError("InfluxDB query failed with status 500")))
    assert resp.status_code == 502
    assert resp.json()["detail"] == "データ量を見積もれませんでした。時間をおいて再試行するか、期間を絞り込んでください"


def test_request_export_rejects_naive_datetime():
    """datetime-localの生値(オフセット無し)はUTC/JSTを判別できないため422で拒否する。"""
    resp, m = _post_export({"start": "2026-10-01T10:00", "end": "2026-10-02T10:00:00Z"})
    assert resp.status_code == 422
    m.assert_not_called()
    resp, m = _post_export({"start": "2026-10-01T10:00:00Z", "end": "2026-10-02T10:00"})
    assert resp.status_code == 422
    m.assert_not_called()


def test_request_export_accepts_offset_datetime_and_passes_aware_values():
    resp, m = _post_export({"start": "2026-10-01T10:00:00+09:00", "end": "2026-10-02T01:00:00.000Z"},
                           MagicMock(return_value=MagicMock(id="j", kind="adhoc", status="queued", requested_at=None)))
    assert resp.status_code == 201
    start, end = m.call_args.args[2], m.call_args.args[3]
    assert start.utcoffset() is not None and end.utcoffset() is not None


def test_request_export_rejects_start_not_before_end():
    for body in ({"start": "2026-10-02T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
                 {"start": "2026-10-01T00:00:00Z", "end": "2026-10-01T00:00:00Z"}):
        resp, m = _post_export(body)
        assert resp.status_code == 422
        m.assert_not_called()


def test_request_export_rejects_unknown_device_with_422():
    from app.services.export_jobs import UnknownDeviceError
    resp, _ = _post_export({"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                            "device_ids": ["x"]},
                           MagicMock(side_effect=UnknownDeviceError(["x"])))
    assert resp.status_code == 422


def test_request_export_rejects_unknown_group_with_422():
    from app.services.device_groups import GroupNotFoundError
    resp, _ = _post_export({"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                            "group_id": "33333333-3333-3333-3333-333333333333"},
                           MagicMock(side_effect=GroupNotFoundError("g")))
    assert resp.status_code == 422


def test_request_export_rejects_non_uuid_group_id():
    resp, m = _post_export({"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
                            "group_id": "not-a-uuid"})
    assert resp.status_code == 422
    m.assert_not_called()


def test_download_export_serves_previous_file_while_regenerating():
    """再生成中(processing)でも前回のファイル(minio_keyあり)はダウンロードできる。"""
    job = MagicMock(status="processing", minio_key="some-key", tenant_id=TENANT_ID)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.stream_export_file", return_value=iter([b"a,b\n"])):
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = job
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/exports/adhoc/download",
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 200


def test_download_export_returns_404_when_no_file():
    job = MagicMock(status="failed", minio_key=None, tenant_id=TENANT_ID)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        db.query.return_value.filter.return_value.first.return_value = job
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/exports/adhoc/download",
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 404


def test_list_exports_returns_empty_list_when_none_exist():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        db = _session_ctx()
        db.query.return_value.filter.return_value.all.return_value = []
        mock_session.return_value = db
        resp = client.get("/tenant-portal/me/exports",
                           cookies={"iot_token": _admin_token()})
    assert resp.status_code == 200
    assert resp.json() == []
