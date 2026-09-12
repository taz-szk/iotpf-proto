import threading
import time

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models.public import Tenant
from app.services.billing import get_effective_retention_days


def sync_tenant_retention(org_id: str, admin_token: str, effective_days: int) -> None:
    """指定テナントのtelemetryバケットのretention ruleを実効値に同期する。
    バケットが存在しなければ何もしない（次回テレメトリ受信時に正しい値で作成される）。
    現在のretentionRulesが既に一致していればPATCHしない。"""
    resp = httpx.get(
        f"{settings.influxdb_url}/api/v2/buckets",
        headers={"Authorization": f"Token {admin_token}"},
        params={"orgID": org_id, "name": "telemetry"},
        timeout=10.0,
    )
    resp.raise_for_status()
    buckets = resp.json().get("buckets", [])
    if not buckets:
        return

    bucket = buckets[0]
    desired_seconds = effective_days * 86400
    current_rules = bucket.get("retentionRules", [])
    current_seconds = current_rules[0]["everySeconds"] if current_rules else 0
    if current_seconds == desired_seconds:
        return

    patch_resp = httpx.patch(
        f"{settings.influxdb_url}/api/v2/buckets/{bucket['id']}",
        headers={"Authorization": f"Token {admin_token}", "Content-Type": "application/json"},
        json={"retentionRules": [{"type": "expire", "everySeconds": desired_seconds}]},
        timeout=10.0,
    )
    patch_resp.raise_for_status()


def run_daily_retention_sync() -> list[dict]:
    """全アクティブテナント（InfluxDB org未設定のものを除く）について、
    実効保持日数をInfluxDBに同期する。戻り値は各テナントの処理結果。"""
    results: list[dict] = []
    with SessionLocal() as db:
        tenants = db.query(Tenant).filter(Tenant.status == "active").all()
        tenant_infos = [
            (str(t.id), t.influxdb_org_id, get_effective_retention_days(db, t))
            for t in tenants if t.influxdb_org_id
        ]

    for tenant_id, org_id, effective_days in tenant_infos:
        try:
            sync_tenant_retention(org_id, settings.influxdb_admin_token, effective_days)
            results.append({"tenant_id": tenant_id, "status": "ok"})
        except Exception as e:
            results.append({"tenant_id": tenant_id, "status": "error", "detail": str(e)})

    return results


def start_data_retention_sync_worker() -> None:
    """毎日1回、全アクティブテナントのデータ保持期間をInfluxDBに同期する
    バックグラウンドスレッドを起動する。既存のstart_billing_batch_worker/
    start_audit_purge_workerと同じdaemon thread+time.sleep(86400)パターン。"""
    def _loop() -> None:
        while True:
            try:
                results = run_daily_retention_sync()
                for r in results:
                    if r.get("status") != "ok":
                        print(f"[data_retention] tenant {r.get('tenant_id')}: {r}")
            except Exception as e:
                print(f"[data_retention] run failed: {e}")
            time.sleep(86400)
    threading.Thread(target=_loop, daemon=True).start()
