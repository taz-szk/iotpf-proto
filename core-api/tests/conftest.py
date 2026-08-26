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
from app.main import app

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
