"""準リアルタイムアクセス機能: InfluxDBの読み取り専用トークンを発行/失効するサービス。
テナント分離はInfluxDB自身のトークンスコープ(orgID指定・read権限のみ)で保証する。
アプリケーション側でtenant_idを比較するような独自の判定ロジックはここに置かない。"""
import httpx
from app.config import settings


class InfluxAuthorizationError(Exception):
    pass


def issue_readonly_token(org_id: str) -> tuple[str, str]:
    """指定org向けの読み取り専用InfluxDB Authorizationを発行し、(authorization_id, 平文token)を返す。"""
    try:
        resp = httpx.post(
            f"{settings.influxdb_url}/api/v2/authorizations",
            headers={"Authorization": f"Token {settings.influxdb_admin_token}", "Content-Type": "application/json"},
            json={
                "orgID": org_id,
                "description": f"readonly-realtime-{org_id}",
                "permissions": [
                    {"action": "read", "resource": {"type": "buckets", "orgID": org_id}},
                ],
            },
            timeout=10.0,
        )
        if resp.status_code != 201:
            resp.raise_for_status()
    except Exception as e:
        raise InfluxAuthorizationError(str(e)) from e
    body = resp.json()
    return body["id"], body["token"]


def revoke_readonly_token(influx_authorization_id: str) -> None:
    """指定authorization_idのInfluxDB Authorizationを削除する。"""
    try:
        resp = httpx.delete(
            f"{settings.influxdb_url}/api/v2/authorizations/{influx_authorization_id}",
            headers={"Authorization": f"Token {settings.influxdb_admin_token}"},
            timeout=10.0,
        )
        if resp.status_code != 204:
            resp.raise_for_status()
    except Exception as e:
        raise InfluxAuthorizationError(str(e)) from e
