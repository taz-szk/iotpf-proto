"""新規テナント(ファームウェア用テーブル未作成)で統計APIが500にならないことの回帰テスト。

firmware_releasesはテナント開通時には作られず、初回のファームウェア操作で遅延作成される。
統計APIがこのテーブルを直接SELECTして失敗をtry/exceptで握り潰すと、PostgreSQLでは
トランザクションが中断状態のまま残り、後続のクエリが全てInFailedSqlTransactionになる。"""
import uuid
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import InternalError, ProgrammingError

from app.main import app
from app.services.auth import create_access_token

_TENANT_ID = "11111111-1111-1111-1111-111111111111"


class _PostgresLikeDb:
    """失敗した文の後は、rollbackするまで後続の文が全て失敗するPostgreSQLの挙動を模したDB。"""

    def __init__(self, firmware_table_exists: bool, firmware_count: int = 0):
        self.firmware_table_exists = firmware_table_exists
        self.firmware_count = firmware_count
        self.aborted = False
        tenant = MagicMock()
        tenant.influxdb_org_id = "org-001"
        tenant.influxdb_token = "tok-001"
        self._tenant = tenant

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _ensure_usable(self):
        if self.aborted:
            raise InternalError("stmt", {}, Exception("current transaction is aborted"))

    def execute(self, stmt, params=None):
        self._ensure_usable()
        sql = str(stmt)
        result = MagicMock()
        if "to_regclass" in sql:
            result.scalar.return_value = "firmware_releases" if self.firmware_table_exists else None
        elif "firmware_releases" in sql:
            if not self.firmware_table_exists:
                self.aborted = True
                raise ProgrammingError("stmt", {}, Exception("relation does not exist"))
            result.scalar.return_value = self.firmware_count
        else:
            result.scalar.return_value = 0
        return result

    def query(self, *args, **kwargs):
        self._ensure_usable()
        q = MagicMock()
        q.filter.return_value.first.return_value = self._tenant
        q.filter.return_value.all.return_value = []
        return q


def _platform_stats(db):
    with patch("app.routers.stats.verify_token", return_value={"sub": str(uuid.uuid4()), "type": "platform"}), \
         patch("app.routers.stats.SessionLocal", return_value=db), \
         patch("app.routers.stats._count_influxdb_points", return_value=0), \
         TestClient(app) as client:
        return client.get(f"/tenants/{_TENANT_ID}/stats", headers={"Authorization": "Bearer dummy"})


def _portal_stats(db):
    jwt = create_access_token({
        "sub": "user-id", "email": "user@acme.com",
        "type": "tenant", "role": "viewer", "tenant_id": _TENANT_ID,
    })
    with patch("app.routers.tenant_portal.SessionLocal", return_value=db), \
         patch("app.routers.tenant_portal._count_influxdb_points", return_value=0), \
         TestClient(app) as client:
        return client.get("/tenant-portal/me/stats", cookies={"iot_token": jwt})


def test_platform_stats_for_tenant_without_firmware_tables():
    resp = _platform_stats(_PostgresLikeDb(firmware_table_exists=False))
    assert resp.status_code == 200
    assert resp.json()["firmware_releases"] == 0


def test_portal_stats_for_tenant_without_firmware_tables():
    resp = _portal_stats(_PostgresLikeDb(firmware_table_exists=False))
    assert resp.status_code == 200
    assert resp.json()["firmware_releases"] == 0


def test_platform_stats_counts_firmware_releases_when_table_exists():
    resp = _platform_stats(_PostgresLikeDb(firmware_table_exists=True, firmware_count=3))
    assert resp.status_code == 200
    assert resp.json()["firmware_releases"] == 3


def test_portal_stats_counts_firmware_releases_when_table_exists():
    resp = _portal_stats(_PostgresLikeDb(firmware_table_exists=True, firmware_count=3))
    assert resp.status_code == 200
    assert resp.json()["firmware_releases"] == 3
