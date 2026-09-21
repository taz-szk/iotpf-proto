import os
import pytest

# テスト用の最低限の環境変数を設定（実際の値は不要）
os.environ.setdefault("POSTGRES_DSN", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test_jwt_secret_for_unit_tests_only_32chars")
os.environ.setdefault("GRAFANA_ADMIN_PASSWORD", "test_grafana_password")
os.environ.setdefault("MINIO_SECRET_KEY", "test_minio_secret")
os.environ.setdefault("EMQX_API_PASSWORD", "test_emqx_password")
os.environ.setdefault("EMQX_WEBHOOK_SECRET", "test_webhook_secret_for_unit_tests")

from fastapi.testclient import TestClient
import app.main as app_main
from app.main import app


@pytest.fixture(autouse=True)
def _no_startup_migrations(monkeypatch):
    # ローカルにPostgresが無いと、起動時の全マイグレーションが接続失敗を待つ(Windowsで1起動約70秒)。
    # マイグレーション自体のテストはapp.databaseの関数を直接呼ぶので影響しない。
    for name in dir(app_main):
        if name.startswith("migrate_"):
            monkeypatch.setattr(app_main, name, lambda: None)


@pytest.fixture(autouse=True)
def _passthrough_tenant_session_check(monkeypatch):
    # テナントセッションのDB再検証(tenant_session.revalidate_session)は、ローカルにDBが無い
    # 既存テストでは素通しにする。再検証そのもののテストはtest_tenant_session.pyで実物に戻して行う。
    from app.services import tenant_session
    monkeypatch.setattr(tenant_session, "revalidate_session", lambda payload: payload)


@pytest.fixture(autouse=True)
def _passthrough_platform_session_check(monkeypatch):
    # プラットフォーム管理者のCookie再検証も同様。実物のテストはtest_platform_session.pyで行う。
    from app.services import platform_session
    monkeypatch.setattr(platform_session, "revalidate_platform_session", lambda payload: None)


@pytest.fixture(autouse=True)
def _no_blocklist_db(monkeypatch):
    # JTIブロックリストのDB永続化は、ローカルにDBが無い既存テストでは何もしない。
    # 永続化そのもののテストはtest_token_blocklist_persistence.pyで差し替えて行う。
    from app.services import token_blocklist
    monkeypatch.setattr(token_blocklist, "_store", lambda jti, exp_epoch: None)
    monkeypatch.setattr(token_blocklist, "_lookup", lambda jti: False)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
