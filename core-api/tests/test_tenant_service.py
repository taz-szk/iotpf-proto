from unittest.mock import patch, MagicMock
import pytest

from app.services.tenant import create_influxdb_bucket


def test_create_influxdb_bucket_sends_retention_rule():
    mock_resp = MagicMock(status_code=201)
    with patch("app.services.tenant.httpx.post", return_value=mock_resp) as mock_post:
        create_influxdb_bucket("org-1", "admin-token", 180)

    mock_post.assert_called_once()
    call = mock_post.call_args
    assert "/api/v2/buckets" in call.args[0]
    assert call.kwargs["json"]["orgID"] == "org-1"
    assert call.kwargs["json"]["name"] == "telemetry"
    assert call.kwargs["json"]["retentionRules"] == [{"type": "expire", "everySeconds": 180 * 86400}]
    assert call.kwargs["headers"]["Authorization"] == "Token admin-token"


def test_create_influxdb_bucket_ignores_already_exists_response():
    mock_resp = MagicMock(status_code=422)
    with patch("app.services.tenant.httpx.post", return_value=mock_resp):
        create_influxdb_bucket("org-1", "admin-token", 180)  # 例外を投げなければ成功


def test_create_influxdb_bucket_raises_on_other_errors():
    mock_resp = MagicMock(status_code=500)
    mock_resp.raise_for_status.side_effect = Exception("server error")
    with patch("app.services.tenant.httpx.post", return_value=mock_resp):
        with pytest.raises(Exception):
            create_influxdb_bucket("org-1", "admin-token", 180)
