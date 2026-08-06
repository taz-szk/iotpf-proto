import httpx
from app.config import settings

_DEFAULT_DASHBOARD = {
    "dashboard": {
        "title": "テレメトリ監視",
        "panels": [
            {
                "id": 1,
                "type": "timeseries",
                "title": "センサー値（時系列）",
                "gridPos": {"x": 0, "y": 0, "w": 24, "h": 10},
                "targets": [
                    {
                        "refId": "A",
                        "datasource": {"type": "influxdb"},
                        "query": 'from(bucket: "telemetry")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r._measurement == "telemetry")\n  |> aggregateWindow(every: v.windowPeriod, fn: mean)\n  |> yield(name: "mean")',
                    }
                ],
                "fieldConfig": {
                    "defaults": {"custom": {"lineWidth": 2}},
                },
                "options": {"tooltip": {"mode": "multi"}},
            }
        ],
        "time": {"from": "now-1h", "to": "now"},
        "refresh": "30s",
        "schemaVersion": 38,
        "version": 0,
    },
    "overwrite": False,
}

def _admin_auth() -> tuple[str, str]:
    return (settings.grafana_admin_user, settings.grafana_admin_password)

def create_grafana_org(name: str) -> int:
    resp = httpx.post(
        f"{settings.grafana_url}/api/orgs",
        auth=_admin_auth(),
        json={"name": name},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()["orgId"]

def setup_grafana_datasource(org_id: int, tenant_name: str, influxdb_org_id: str, influxdb_token: str) -> None:
    resp = httpx.post(
        f"{settings.grafana_url}/api/datasources",
        auth=_admin_auth(),
        headers={"X-Grafana-Org-Id": str(org_id)},
        json={
            "name": f"InfluxDB-{tenant_name}",
            "type": "influxdb",
            "url": "http://influxdb:8086",
            "access": "proxy",
            "isDefault": True,
            "jsonData": {
                "version": "Flux",
                "organization": influxdb_org_id,
                "defaultBucket": "telemetry",
            },
            "secureJsonData": {"token": influxdb_token},
        },
        timeout=10.0,
    )
    resp.raise_for_status()

def create_default_dashboard(org_id: int, tenant_name: str) -> None:
    dashboard = dict(_DEFAULT_DASHBOARD)
    dashboard["dashboard"] = dict(dashboard["dashboard"])
    dashboard["dashboard"]["title"] = f"テレメトリ監視 - {tenant_name}"
    resp = httpx.post(
        f"{settings.grafana_url}/api/dashboards/db",
        auth=_admin_auth(),
        headers={"X-Grafana-Org-Id": str(org_id)},
        json=dashboard,
        timeout=10.0,
    )
    resp.raise_for_status()

def provision_tenant_grafana(tenant_name: str, influxdb_org_id: str, influxdb_token: str) -> int:
    """テナント用Grafana Orgを作成しDataSource・ダッシュボードを設定する。Org IDを返す。"""
    org_id = create_grafana_org(tenant_name)
    setup_grafana_datasource(org_id, tenant_name, influxdb_org_id, influxdb_token)
    create_default_dashboard(org_id, tenant_name)
    return org_id
