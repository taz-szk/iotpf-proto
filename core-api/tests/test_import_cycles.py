"""テナント向けAIアシスタント機能追加により発生した循環importの再発防止テスト。

最終レビューで発見: app.services.rag / app.routers.rag / app.services.rag_tools /
app.services.rag_tools.tenant をapp.main経由ではなく単独のエントリポイントとして
importすると、tenant_portal.pyとの間の循環importでImportErrorになっていた
（app.mainのimport順序に依存する脆弱な状態だった）。
各モジュールが単独でimportできることを、フレッシュなサブプロセスで検証する
（同一プロセス内ではsys.modulesにキャッシュされ再現しないため）。
"""

import subprocess
import sys

import pytest

_ENTRY_POINTS = [
    "app.routers.rag",
    "app.services.rag",
    "app.services.rag_tools",
    "app.services.rag_tools.tenant",
]


@pytest.mark.parametrize("module_name", _ENTRY_POINTS)
def test_module_importable_as_standalone_entry_point(module_name):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module_name}"],
        capture_output=True, text=True, cwd=None,
    )
    assert result.returncode == 0, (
        f"import {module_name} failed as a standalone entry point:\n{result.stderr}"
    )
