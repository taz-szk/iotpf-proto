from unittest.mock import patch, MagicMock
from app.services.grafana import create_grafana_org, setup_grafana_datasource, create_default_dashboard

def _mock_resp(status=200, json_data=None):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = json_data or {}
    m.raise_for_status = MagicMock()
    return m

def test_create_grafana_org_returns_org_id():
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.post.return_value = _mock_resp(200, {"orgId": 42, "message": "Organization created"})
        result = create_grafana_org("test-tenant")
    assert result == 42
    mock_httpx.post.assert_called_once()

def test_setup_grafana_datasource_calls_api():
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.post.return_value = _mock_resp(200, {"id": 1, "message": "Datasource added"})
        setup_grafana_datasource(
            org_id=42,
            tenant_name="test-tenant",
            influxdb_org_id="org-001",
            influxdb_token="token-001",
        )
    assert mock_httpx.post.called

def test_create_default_dashboard_calls_api():
    with patch("app.services.grafana.httpx") as mock_httpx:
        mock_httpx.post.return_value = _mock_resp(200, {"id": 1, "uid": "abc", "url": "/d/abc"})
        create_default_dashboard(org_id=42, tenant_name="test-tenant")
    assert mock_httpx.post.called
