import httpx
import secrets
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

def ensure_grafana_user_in_org(org_id: int, email: str, grafana_role: str) -> None:
    """Grafana org_id にユーザーを追加する。存在しなければ作成する。"""
    # 1. ユーザー存在確認
    lookup = httpx.get(
        f"{settings.grafana_url}/api/users/lookup",
        params={"loginOrEmail": email},
        auth=_admin_auth(), timeout=10.0,
    )
    if lookup.status_code == 404:
        # 2. ユーザー作成（パスワードは使わない — Auth Proxy が認証するため）
        create = httpx.post(
            f"{settings.grafana_url}/api/admin/users",
            auth=_admin_auth(),
            json={"name": email, "email": email, "login": email,
                  "password": secrets.token_hex(16)},
            timeout=10.0,
        )
        create.raise_for_status()
    else:
        lookup.raise_for_status()

    # 3. org に追加（409 = すでにメンバー → 無視）
    add = httpx.post(
        f"{settings.grafana_url}/api/orgs/{org_id}/users",
        auth=_admin_auth(),
        json={"loginOrEmail": email, "role": grafana_role},
        timeout=10.0,
    )
    if add.status_code not in (200, 409):
        add.raise_for_status()

def provision_tenant_grafana(tenant_name: str, influxdb_org_id: str, influxdb_token: str) -> int:
    """テナント用Grafana Orgを作成しDataSource・ダッシュボードを設定する。Org IDを返す。"""
    org_id = create_grafana_org(tenant_name)
    setup_grafana_datasource(org_id, tenant_name, influxdb_org_id, influxdb_token)
    create_default_dashboard(org_id, tenant_name)
    return org_id
