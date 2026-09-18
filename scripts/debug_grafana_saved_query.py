"""Grafanaの各テナントのホームダッシュボードに実際に保存されている
archived_device_name変数のFluxクエリ文字列を取得し、コード側の
_FLUX_DEVICE_VAR_ARCHIVEDと完全一致するか比較するデバッグスクリプト。"""
import httpx
from app.database import SessionLocal
from app.config import settings
from app.models.public import Tenant
from app.services.grafana import _FLUX_DEVICE_VAR_ARCHIVED, _admin_auth

with SessionLocal() as db:
    tenants = db.query(Tenant).filter(
        Tenant.status == "active", Tenant.grafana_org_id.isnot(None)
    ).all()
    tenant_infos = [(t.name, t.grafana_org_id) for t in tenants]

auth = _admin_auth()

for name, org_id in tenant_infos:
    prefs = httpx.get(
        f"{settings.grafana_url}/api/org/preferences",
        auth=auth,
        headers={"X-Grafana-Org-Id": str(org_id)},
        timeout=10.0,
    )
    uid = prefs.json().get("homeDashboardUID") if prefs.status_code == 200 else None
    if not uid:
        print(f"tenant={name}: home dashboard UID なし (status={prefs.status_code})")
        continue

    dash_resp = httpx.get(
        f"{settings.grafana_url}/api/dashboards/uid/{uid}",
        auth=auth,
        headers={"X-Grafana-Org-Id": str(org_id)},
        timeout=10.0,
    )
    if dash_resp.status_code != 200:
        print(f"tenant={name}: dashboard取得失敗 status={dash_resp.status_code}")
        continue

    templating = dash_resp.json()["dashboard"].get("templating", {}).get("list", [])
    var = next((v for v in templating if v.get("name") == "archived_device_name"), None)
    if not var:
        print(f"tenant={name}: archived_device_name変数が見つかりません(テンプレート一覧: {[v.get('name') for v in templating]})")
        continue

    saved_query = var.get("query", {}).get("query", "")
    print(f"\n########## tenant={name} org_id={org_id} uid={uid} ##########")
    print(f"変数のcurrent: {var.get('current')}")
    print(f"refresh: {var.get('refresh')}  sort: {var.get('sort')}  multi: {var.get('multi')}  includeAll: {var.get('includeAll')}")
    print(f"保存済みクエリとコード側の定数が完全一致: {saved_query == _FLUX_DEVICE_VAR_ARCHIVED}")
    if saved_query != _FLUX_DEVICE_VAR_ARCHIVED:
        print("--- 保存済みクエリ ---")
        print(repr(saved_query))
        print("--- コード側の定数 ---")
        print(repr(_FLUX_DEVICE_VAR_ARCHIVED))
