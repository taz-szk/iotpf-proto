import httpx
from sqlalchemy import text
from app.config import settings
from app.database import create_tenant_schema, engine, SessionLocal
from app.services.grafana import provision_tenant_grafana, get_or_create_platform_org, remove_tenant_datasource_from_platform_org

def create_influxdb_org(name: str, admin_token: str) -> dict:
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/orgs",
        headers={"Authorization": f"Token {admin_token}", "Content-Type": "application/json"},
        json={"name": name},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()

def create_influxdb_token_for_org(org_id: str, admin_token: str) -> str:
    resp = httpx.post(
        f"{settings.influxdb_url}/api/v2/authorizations",
        headers={"Authorization": f"Token {admin_token}", "Content-Type": "application/json"},
        json={
            "orgID": org_id,
            "description": f"org-{org_id}-token",
            "permissions": [
                {"action": "read", "resource": {"type": "buckets", "orgID": org_id}},
                {"action": "write", "resource": {"type": "buckets", "orgID": org_id}},
            ],
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()["token"]

def teardown_tenant(tenant_id: str, influxdb_org_id: str | None, grafana_org_id: str | None) -> None:
    """テナントの全リソースを削除する。BackgroundTask として呼び出す。"""

    # 1. MinIO: テナントのファームウェアオブジェクト削除
    try:
        from app.services.minio_client import delete_all_tenant_firmware
        delete_all_tenant_firmware(tenant_id)
        print(f"[teardown] MinIO objects deleted: tenant={tenant_id}")
    except Exception as e:
        print(f"[teardown] MinIO cleanup failed: {e}")

    # 2. PostgreSQL: テナントスキーマを DROP (テーブル・データをすべて削除)
    try:
        schema = f"tenant_{tenant_id.replace('-', '_')}"
        with engine.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.commit()
        print(f"[teardown] Schema dropped: {schema}")
    except Exception as e:
        print(f"[teardown] Schema drop failed: {e}")

    # 3. PostgreSQL public: tenant 行を削除 (provisioning_tokens は CASCADE)
    try:
        from app.models.public import Tenant
        with SessionLocal() as db:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                db.delete(tenant)
                db.commit()
        print(f"[teardown] Tenant row deleted: {tenant_id}")
    except Exception as e:
        print(f"[teardown] Tenant row deletion failed: {e}")

    # 4. InfluxDB: org 削除 (バケット・テレメトリデータも全削除)
    if influxdb_org_id:
        try:
            resp = httpx.delete(
                f"{settings.influxdb_url}/api/v2/orgs/{influxdb_org_id}",
                headers={"Authorization": f"Token {settings.influxdb_admin_token}"},
                timeout=60.0,
            )
            resp.raise_for_status()
            print(f"[teardown] InfluxDB org deleted: {influxdb_org_id}")
        except Exception as e:
            print(f"[teardown] InfluxDB org deletion failed: {e}")

    # 5. プラットフォーム管理 org からテナントのデータソースを削除
    # tenant_id が InfluxDB org 名として使われているのでそのまま検索キーにする
    try:
        platform_org_id = get_or_create_platform_org()
        remove_tenant_datasource_from_platform_org(platform_org_id, tenant_id)
        print(f"[teardown] Platform org datasource removed: tenant={tenant_id}")
    except Exception as e:
        print(f"[teardown] Platform org datasource removal failed: {e}")

    # 6. Grafana: org 削除 (ダッシュボード・データソース・ユーザーも削除)
    if grafana_org_id:
        try:
            # Grafana は org にユーザーが残っていると削除できないため先に全ユーザーを除去する
            users_resp = httpx.get(
                f"{settings.grafana_url}/api/orgs/{grafana_org_id}/users",
                auth=(settings.grafana_admin_user, settings.grafana_admin_password),
                timeout=10.0,
            )
            if users_resp.status_code == 200:
                for u in users_resp.json():
                    httpx.delete(
                        f"{settings.grafana_url}/api/orgs/{grafana_org_id}/users/{u['userId']}",
                        auth=(settings.grafana_admin_user, settings.grafana_admin_password),
                        timeout=10.0,
                    )
            resp = httpx.delete(
                f"{settings.grafana_url}/api/orgs/{grafana_org_id}",
                auth=(settings.grafana_admin_user, settings.grafana_admin_password),
                timeout=30.0,
            )
            if resp.status_code not in (200, 404):
                resp.raise_for_status()
            print(f"[teardown] Grafana org deleted: {grafana_org_id}")
        except Exception as e:
            print(f"[teardown] Grafana org deletion failed: {e}")


def setup_tenant(tenant_id: str, tenant_name: str) -> tuple[str, str, int]:
    """
    Returns: (influxdb_org_id, influxdb_token, grafana_org_id)
    InfluxDB/Grafana org 名は tenant_id (UUID) を使用する。
    表示名ではなく一意な ID を使うことで、同一テナント名・スラッグの再作成時の衝突を防ぐ。
    """
    org = create_influxdb_org(tenant_id, settings.influxdb_admin_token)
    org_id = org["id"]
    token = create_influxdb_token_for_org(org_id, settings.influxdb_admin_token)
    create_tenant_schema(tenant_id)
    grafana_org_id = provision_tenant_grafana(tenant_name, org_id, token, org_name=tenant_id)
    return org_id, token, grafana_org_id
