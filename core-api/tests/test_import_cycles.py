"""テナント向けAIアシスタント機能追加により発生した循環importの再発防止テスト。

最終レビューで発見: app.services.rag / app.routers.rag / app.services.rag_tools /
app.services.rag_tools.tenant をapp.main経由ではなく単独のエントリポイントとして
importすると、tenant_portal.pyとの間の循環importでImportErrorになっていた
（app.mainのimport順序に依存する脆弱な状態だった）。
各モジュールが単独でimportできることを、フレッシュなサブプロセスで検証する
（同一プロセス内ではsys.modulesにキャッシュされ再現しないため）。

サブプロセスはconftest.pyのos.environ.setdefault群を引き継がないため、
importエラーの検証がpydantic-settingsの必須環境変数エラーに埋もれないよう、
同じ最低限の環境変数とcore-apiディレクトリを明示的に指定する。
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ENTRY_POINTS = [
    "app.routers.rag",
    "app.services.rag",
    "app.services.rag_tools",
    "app.services.rag_tools.tenant",
]

_CORE_API_DIR = Path(__file__).resolve().parent.parent

_TEST_ENV = {
    "POSTGRES_DSN": "postgresql://test:test@localhost:5432/test",
    "JWT_SECRET": "test_jwt_secret_for_unit_tests_only_32chars",
    "GRAFANA_ADMIN_PASSWORD": "test_grafana_password",
    "MINIO_SECRET_KEY": "test_minio_secret",
    "EMQX_API_PASSWORD": "test_emqx_password",
    "EMQX_WEBHOOK_SECRET": "test_webhook_secret_for_unit_tests",
}


@pytest.mark.parametrize("module_name", _ENTRY_POINTS)
def test_module_importable_as_standalone_entry_point(module_name):
    env = {**os.environ, **_TEST_ENV}
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        capture_output=True, text=True, cwd=str(_CORE_API_DIR), env=env,
    )
    assert result.returncode == 0, (
        f"import {module_name} failed as a standalone entry point:\n{result.stderr}"
    )
