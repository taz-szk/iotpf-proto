# ローカルLLM RAG+エージェント機能 Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ローカルLLM（Ollama）+pgvectorによるドキュメントQ&A機能と、確認フロー付きでプラットフォーム操作（プロビジョニングトークン発行・ダッシュボード設定・アラートルール作成の3ドメイン）を代行実行できるPF管理者向けアシスタントを実装する。

**Architecture:** 新規docker-composeサービス`ollama`＋既存postgresへのpgvector拡張追加。`core-api`に新規モジュール群（Ollama HTTPクライアント、ドキュメントチャンク化、ツールレジストリ、チャットオーケストレーション）を追加し、既存のPF管理者向けAPI（プロビジョニングトークン発行・アラートルール作成・ダッシュボード設定）を薄くラップするツールとして呼び出す。新規マイクロサービスは作らない。

**Tech Stack:** FastAPI, SQLAlchemy, pgvector, httpx（Ollama OpenAI互換エンドポイント呼び出し）, Alpine.js, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-09-12-rag-assistant-design.md`

## Global Constraints

- クラウドAPIは一切使用しない。生成・埋め込みともにOllama（ローカル）のみ。
- Ollamaとの通信はOpenAI互換エンドポイント（`POST /v1/chat/completions`、`POST /v1/embeddings`）のみを使う。Ollama固有のSDK/独自形式には依存しない。
- 新規Pythonライブラリは追加しない（既存の`httpx`のみで実装する）。**例外:** `pgvector`パッケージのみ、ORMでベクトル型（`Vector`）を正しく扱うための最小限の依存として追加を許可する（Task 4参照）。HTTP呼び出しロジックに新規ライブラリを持ち込むことは引き続き禁止。
- postgresイメージを`postgres:16-alpine`から`pgvector/pgvector:pg16`に切り替える（データボリュームは互換、移行不要）。
- アクション実行ツール（状態変更を伴うもの）は必ず「提案→確認→実行」のフローを経る。読み取り専用ツールは即時実行する。
- ツールハンドラは既存のPF管理者向けAPIエンドポイント関数を直接呼ぶ薄いラッパーとする。新規ビジネスロジックは書かない。
- 会話履歴はDBに保存しない。保存するのは未確認アクション（`agent_pending_actions`、10分で失効）のみ。
- ツール実行時、ハンドラに渡す`tenant_id`は必ずURLパス（認証済み）由来の値を使う。モデルが返した`tool_args`内の値は信用しない。
- 対象ドキュメントは`docs/design.html`・`docs/device-guide.html`・`docs/install-guide.html`・`docs/superpowers/specs/*.md`・`CLAUDE.md`のみ。`docs/superpowers/plans/*.md`は対象外。

---

### Task 1: インフラ基盤（docker-compose + DBスキーマ）

**Files:**
- Modify: `docker-compose.yml`（postgresイメージ切り替え、`ollama`サービス追加、`ollama_data`ボリューム追加、core-apiへの`OLLAMA_URL`等の環境変数追加）
- Modify: `core-api/app/config.py`（Ollama関連設定を追加）
- Modify: `core-api/app/database.py`（末尾、`migrate_add_data_retention_columns`の後に追記）
- Modify: `core-api/app/main.py`（マイグレーション関数のimport・起動時実行リストに追加）
- Test: `core-api/tests/test_db.py`（新規マイグレーション関数のテスト追加）

**Interfaces:**
- Produces: `settings.ollama_url`（`str`）, `settings.ollama_chat_model`（`str`）, `settings.ollama_embed_model`（`str`）。`doc_chunks`テーブル（`id, source_path, heading, content, embedding VECTOR(768), created_at`）。`agent_pending_actions`テーブル（`id, tenant_id, tool_name, tool_args JSONB, requested_by, created_at, expires_at`）。これらは後続の全タスクが使う。

- [ ] **Step 1: `docker-compose.yml`を更新する**

以下の行:

```yaml
  postgres:
    image: postgres:16-alpine
```

を以下に置き換え:

```yaml
  postgres:
    image: pgvector/pgvector:pg16
```

（データボリューム`postgres_data`・初期化スクリプト`postgres/init/`は互換性がありそのまま使える。）

以下の行:

```yaml
volumes:
  postgres_data:
  influxdb_data:
  emqx_data:
  minio_data:
  grafana_data:
  step_ca_data:
  mailhog_data:
```

を以下に置き換え:

```yaml
volumes:
  postgres_data:
  influxdb_data:
  emqx_data:
  minio_data:
  grafana_data:
  step_ca_data:
  mailhog_data:
  ollama_data:
```

`core-api`サービス定義の直前（ファイル内で`core-api:`が最初に登場する行の前）に、新規`ollama`サービスを追記:

```yaml
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    restart: unless-stopped
    networks: [internal]
    volumes:
      - ollama_data:/root/.ollama
    expose:
      - "11434"

```

`core-api`サービスの`environment:`ブロック内、以下の行:

```yaml
      SMTP_FROM: "${SMTP_FROM:-alerts@iot-platform.local}"
```

を以下に置き換え:

```yaml
      SMTP_FROM: "${SMTP_FROM:-alerts@iot-platform.local}"
      OLLAMA_URL: "http://ollama:11434"
      OLLAMA_CHAT_MODEL: "${OLLAMA_CHAT_MODEL:-qwen2.5:3b}"
      OLLAMA_EMBED_MODEL: "${OLLAMA_EMBED_MODEL:-nomic-embed-text}"
```

- [ ] **Step 2: `docker compose config`で構文検証する**

Run: `docker compose config --quiet`
Expected: エラーなし

- [ ] **Step 3: `config.py`に設定を追加する**

`core-api/app/config.py`の以下の行:

```python
    smtp_from: str = "alerts@iot-platform.local"
```

を以下に置き換え:

```python
    smtp_from: str = "alerts@iot-platform.local"
    ollama_url: str = "http://ollama:11434"
    ollama_chat_model: str = "qwen2.5:3b"
    ollama_embed_model: str = "nomic-embed-text"
```

- [ ] **Step 4: 失敗するテストを書く（マイグレーション関数）**

`core-api/tests/test_db.py`の末尾に追記:

```python
def test_migrate_create_rag_tables_executes():
    from app.database import migrate_create_rag_tables
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.database.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        migrate_create_rag_tables()
    sql_calls = _sql_text(mock_conn.execute.call_args_list)
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql_calls
    assert "doc_chunks" in sql_calls and "VECTOR(768)" in sql_calls
    assert "agent_pending_actions" in sql_calls and "JSONB" in sql_calls
```

- [ ] **Step 5: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py::test_migrate_create_rag_tables_executes -v`
Expected: FAIL（`ImportError: cannot import name 'migrate_create_rag_tables'`）

- [ ] **Step 6: `database.py`にマイグレーション関数を追加する**

`core-api/app/database.py`の末尾（`migrate_add_data_retention_columns`関数の後）に追記:

```python


def migrate_create_rag_tables() -> None:
    """RAGアシスタント機能用のテーブル（doc_chunks, agent_pending_actions）を
    作成する（べき等）。pgvector拡張の有効化も含む。"""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS doc_chunks (
                id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_path VARCHAR(500) NOT NULL,
                heading     VARCHAR(500),
                content     TEXT NOT NULL,
                embedding   VECTOR(768),
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_pending_actions (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                tool_name    VARCHAR(100) NOT NULL,
                tool_args    JSONB NOT NULL,
                requested_by VARCHAR(255) NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expires_at   TIMESTAMPTZ NOT NULL
            )
        """))
        conn.commit()
```

- [ ] **Step 7: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py::test_migrate_create_rag_tables_executes -v`
Expected: PASS

- [ ] **Step 8: main.pyに配線する**

`core-api/app/main.py`の以下の行:

```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns, migrate_add_data_retention_columns
```

を以下に置き換え:

```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns, migrate_add_data_retention_columns, migrate_create_rag_tables
```

以下の行:

```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns, migrate_add_data_retention_columns):
```

を以下に置き換え:

```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id, migrate_create_billing_tables, migrate_create_billing_default_prices, migrate_create_billing_settings, migrate_add_bill_shock_threshold_columns, migrate_add_data_retention_columns, migrate_create_rag_tables):
```

- [ ] **Step 9: 全テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py -v`
Expected: PASS（全件）

- [ ] **Step 10: コミット**

```bash
git add docker-compose.yml core-api/app/config.py core-api/app/database.py core-api/app/main.py core-api/tests/test_db.py
git commit -m "feat: RAGアシスタント機能のインフラ基盤(Ollama+pgvector)を追加"
```

**注記（実装完了後、手動で1回実行する運用手順。自動化はしない）:**
```bash
docker compose up -d ollama
docker compose exec ollama ollama pull qwen2.5:3b
docker compose exec ollama ollama pull nomic-embed-text
```

---

### Task 2: Ollama HTTPクライアント

**Files:**
- Create: `core-api/app/services/ollama_client.py`
- Test: `core-api/tests/test_ollama_client.py`

**Interfaces:**
- Consumes: `settings.ollama_url`, `settings.ollama_chat_model`, `settings.ollama_embed_model`（Task 1）
- Produces:
  - `embed(text: str) -> list[float]`
  - `chat(messages: list[dict], tools: list[dict] | None = None) -> dict`（Ollamaの`/v1/chat/completions`レスポンスの`choices[0].message`部分を返す。`{"role": "assistant", "content": str | None, "tool_calls": list[dict] | None}`の形）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_ollama_client.py`を新規作成:

```python
from unittest.mock import patch, MagicMock

from app.services.ollama_client import chat, embed


def test_embed_sends_correct_request_and_returns_vector():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        result = embed("何か質問文")

    assert result == [0.1, 0.2, 0.3]
    call = mock_post.call_args
    assert "/v1/embeddings" in call.args[0]
    assert call.kwargs["json"]["input"] == "何か質問文"
    assert call.kwargs["json"]["model"] == "qwen2.5:3b" or "model" in call.kwargs["json"]


def test_chat_sends_messages_and_returns_message_dict():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": "回答です", "tool_calls": None}}]
    }
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        result = chat(messages=[{"role": "user", "content": "質問"}])

    assert result == {"role": "assistant", "content": "回答です", "tool_calls": None}
    call = mock_post.call_args
    assert "/v1/chat/completions" in call.args[0]
    assert call.kwargs["json"]["messages"] == [{"role": "user", "content": "質問"}]
    assert "tools" not in call.kwargs["json"]


def test_chat_includes_tools_when_provided():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]}}]
    }
    tools = [{"type": "function", "function": {"name": "some_tool"}}]
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp) as mock_post:
        result = chat(messages=[{"role": "user", "content": "質問"}], tools=tools)

    assert result["tool_calls"] == [{"id": "1"}]
    call = mock_post.call_args
    assert call.kwargs["json"]["tools"] == tools


def test_chat_raises_on_error_status():
    mock_resp = MagicMock(status_code=500)
    mock_resp.raise_for_status.side_effect = Exception("ollama down")
    with patch("app.services.ollama_client.httpx.post", return_value=mock_resp):
        with pytest.raises(Exception):
            chat(messages=[{"role": "user", "content": "質問"}])
```

（このテストファイルの先頭に`import pytest`を追加すること。）

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_ollama_client.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.services.ollama_client'`）

- [ ] **Step 3: `ollama_client.py`を新規作成する**

`core-api/app/services/ollama_client.py`:

```python
import httpx

from app.config import settings


def embed(text: str) -> list[float]:
    """テキストをOllamaの埋め込みモデルでベクトル化する。"""
    resp = httpx.post(
        f"{settings.ollama_url}/v1/embeddings",
        json={"model": settings.ollama_embed_model, "input": text},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def chat(messages: list[dict], tools: list[dict] | None = None) -> dict:
    """OllamaのOpenAI互換チャットエンドポイントを呼び、assistantメッセージ部分を返す。
    戻り値の形: {"role": "assistant", "content": str | None, "tool_calls": list[dict] | None}"""
    body = {"model": settings.ollama_chat_model, "messages": messages}
    if tools:
        body["tools"] = tools
    resp = httpx.post(
        f"{settings.ollama_url}/v1/chat/completions",
        json=body,
        timeout=120.0,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_ollama_client.py -v`
Expected: PASS（全4件）

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/ollama_client.py core-api/tests/test_ollama_client.py
git commit -m "feat: OllamaのOpenAI互換エンドポイント呼び出しクライアントを追加"
```

---

### Task 3: ドキュメントチャンク化

**Files:**
- Create: `core-api/app/services/doc_chunking.py`
- Test: `core-api/tests/test_doc_chunking.py`

**Interfaces:**
- Produces: `chunk_markdown(content: str) -> list[dict]`、`chunk_html(content: str) -> list[dict]`。いずれも`[{"heading": str | None, "content": str}, ...]`を返す（`source_path`はTask 4側で付与する、この層の関心事ではない）。

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_doc_chunking.py`を新規作成:

```python
from app.services.doc_chunking import chunk_html, chunk_markdown


def test_chunk_markdown_splits_by_heading():
    content = """# タイトル

イントロ文。

## セクション1

セクション1の本文です。

## セクション2

セクション2の本文です。
"""
    chunks = chunk_markdown(content)
    headings = [c["heading"] for c in chunks]
    assert "セクション1" in headings
    assert "セクション2" in headings
    section1 = next(c for c in chunks if c["heading"] == "セクション1")
    assert "セクション1の本文です" in section1["content"]


def test_chunk_markdown_handles_no_headings():
    content = "見出しなしの本文だけのドキュメント。"
    chunks = chunk_markdown(content)
    assert len(chunks) == 1
    assert chunks[0]["heading"] is None
    assert "見出しなしの本文" in chunks[0]["content"]


def test_chunk_html_splits_by_h2_and_strips_tags():
    content = """
    <html><body>
    <h1>design</h1>
    <h2>API一覧</h2>
    <p>ここにAPIの説明が入ります。</p>
    <h2>データモデル</h2>
    <p>ここにデータモデルの説明が入ります。</p>
    </body></html>
    """
    chunks = chunk_html(content)
    headings = [c["heading"] for c in chunks]
    assert "API一覧" in headings
    assert "データモデル" in headings
    api_chunk = next(c for c in chunks if c["heading"] == "API一覧")
    assert "<p>" not in api_chunk["content"]
    assert "ここにAPIの説明が入ります" in api_chunk["content"]


def test_chunk_html_handles_h3_sections():
    content = "<h2>親</h2><p>親の説明</p><h3>子</h3><p>子の説明</p>"
    chunks = chunk_html(content)
    headings = [c["heading"] for c in chunks]
    assert "親" in headings
    assert "子" in headings
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_doc_chunking.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: `doc_chunking.py`を新規作成する**

`core-api/app/services/doc_chunking.py`:

```python
import re


def chunk_markdown(content: str) -> list[dict]:
    """Markdownを見出し(#〜###)単位で分割する。見出しが無ければ全体を1チャンクにする。"""
    lines = content.split("\n")
    chunks: list[dict] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    def _flush():
        text = "\n".join(current_lines).strip()
        if text:
            chunks.append({"heading": current_heading, "content": text})

    for line in lines:
        m = re.match(r"^(#{1,3})\s+(.+)$", line)
        if m:
            _flush()
            current_heading = m.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)
    _flush()

    return chunks


def chunk_html(content: str) -> list[dict]:
    """HTMLを<h2>/<h3>セクション単位で分割し、タグを除去したテキストにする。"""
    # <h2>または<h3>タグで本文を分割する
    parts = re.split(r"<h[23][^>]*>(.*?)</h[23]>", content, flags=re.DOTALL)
    # parts[0] は最初の見出しより前の部分（無視する）。以降は [heading, body, heading, body, ...] の繰り返し
    chunks: list[dict] = []
    for i in range(1, len(parts), 2):
        heading = _strip_tags(parts[i]).strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        text = _strip_tags(body).strip()
        if text:
            chunks.append({"heading": heading, "content": text})
    return chunks


def _strip_tags(html_fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_doc_chunking.py -v`
Expected: PASS（全4件）

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/doc_chunking.py core-api/tests/test_doc_chunking.py
git commit -m "feat: ドキュメントのMarkdown/HTML見出し単位チャンク化を追加"
```

---

### Task 4: インデックス作成パイプライン

**Files:**
- Create: `core-api/app/services/rag_indexing.py`
- Test: `core-api/tests/test_rag_indexing.py`

**Interfaces:**
- Consumes: `chunk_markdown`, `chunk_html`（Task 3）、`embed`（Task 2）、`doc_chunks`テーブル（Task 1）
- Produces: `reindex_all_documents(db: Session) -> int`（インデックスしたチャンク数を返す。Task 7の`app/routers/rag.py`が使う。仕様書§7の通り、再インデックスAPIも含む全エンドポイントは`app/routers/rag.py`に集約するため、このタスクではルーターを作らずサービス層のみ実装する）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_rag_indexing.py`を新規作成:

```python
from unittest.mock import patch, mock_open, MagicMock

from app.services.rag_indexing import DOCUMENT_GLOBS, reindex_all_documents


def test_document_globs_excludes_plans_directory():
    globs_str = " ".join(DOCUMENT_GLOBS)
    assert "specs" in globs_str
    assert "plans" not in globs_str


def test_reindex_all_documents_replaces_existing_chunks():
    mock_db = MagicMock()
    fake_md_path = MagicMock()
    fake_md_path.__str__ = lambda self: "docs/superpowers/specs/example.md"
    fake_md_path.suffix = ".md"

    with patch("app.services.rag_indexing._iter_target_files", return_value=[fake_md_path]), \
         patch("app.services.rag_indexing.open", mock_open(read_data="# タイトル\n\n## セクション\n\n本文です。\n")), \
         patch("app.services.rag_indexing.embed", return_value=[0.1] * 768) as mock_embed:
        count = reindex_all_documents(mock_db)

    mock_db.query.return_value.delete.assert_called_once()
    assert mock_db.add.called
    assert count == mock_db.add.call_count
    mock_embed.assert_called()
    mock_db.commit.assert_called_once()
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_rag_indexing.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: `rag_indexing.py`を新規作成する**

`core-api/app/services/rag_indexing.py`:

```python
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.rag import DocChunk
from app.services.doc_chunking import chunk_html, chunk_markdown
from app.services.ollama_client import embed

REPO_ROOT = Path(__file__).resolve().parents[3]

DOCUMENT_GLOBS = [
    "docs/design.html",
    "docs/device-guide.html",
    "docs/install-guide.html",
    "docs/superpowers/specs/*.md",
    "CLAUDE.md",
]


def _iter_target_files() -> list[Path]:
    files: list[Path] = []
    for pattern in DOCUMENT_GLOBS:
        files.extend(sorted(REPO_ROOT.glob(pattern)))
    return files


def reindex_all_documents(db: Session) -> int:
    """対象ドキュメント全てを読み直し、doc_chunksを全削除→再構築する。
    戻り値は生成したチャンク数。"""
    db.query(DocChunk).delete()

    count = 0
    for path in _iter_target_files():
        with open(path, encoding="utf-8") as f:
            content = f.read()

        if path.suffix == ".md":
            chunks = chunk_markdown(content)
        else:
            chunks = chunk_html(content)

        source_path = str(path.relative_to(REPO_ROOT))
        for chunk in chunks:
            vector = embed(chunk["content"])
            db.add(DocChunk(
                source_path=source_path,
                heading=chunk["heading"],
                content=chunk["content"],
                embedding=vector,
            ))
            count += 1

    db.commit()
    return count
```

`core-api/app/models/rag.py`を新規作成（`DocChunk`のSQLAlchemyモデル。`pgvector`のSQLAlchemy用型`Vector`を使う）:

```python
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class DocChunk(Base):
    __tablename__ = "doc_chunks"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_path = Column(String(500), nullable=False)
    heading = Column(String(500), nullable=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(768), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

**注記:** `DocChunk`モデルはpgvectorのSQLAlchemy統合を使うため、`pgvector`Pythonパッケージ（`pip install pgvector`）が必要になる。これはGlobal Constraintsの「新規Pythonライブラリは追加しない」の例外とする——ORMでベクトル型を正しく扱うための最小限の依存であり、HTTP呼び出しロジックに新規ライブラリを持ち込むわけではない。`core-api/requirements.txt`に`pgvector`を追記すること。

- [ ] **Step 4: `requirements.txt`に依存を追加する**

`core-api/requirements.txt`の末尾に`pgvector`を追記する（既存の記法に合わせてバージョン指定の要否を確認する。既存ファイルに他パッケージがバージョンピン付きで書かれていればそれに合わせ、無ければ`pgvector`のみでよい）。

- [ ] **Step 5: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_rag_indexing.py -v`
Expected: PASS（全2件）

- [ ] **Step 6: コミット**

```bash
git add core-api/app/services/rag_indexing.py core-api/app/models/rag.py core-api/requirements.txt core-api/tests/test_rag_indexing.py
git commit -m "feat: ドキュメントのインデックス作成パイプラインを追加"
```

**注記:** 仕様書§7では`POST /platform/assistant/reindex`を含む3エンドポイント全てを`app/routers/rag.py`に集約すると定めている。このタスクではサービス層（`reindex_all_documents`）のみを作り、エンドポイント自体の実装はTask 7で行う（`app/routers/rag.py`がまだ存在しないため）。

---

### Task 5: ツールレジストリ＋Phase 1の3ドメイン

**Files:**
- Create: `core-api/app/services/rag_tools/__init__.py`
- Create: `core-api/app/services/rag_tools/provisioning.py`
- Create: `core-api/app/services/rag_tools/dashboard.py`
- Create: `core-api/app/services/rag_tools/alerts.py`
- Create: `core-api/app/services/rag_tools/general.py`
- Test: `core-api/tests/test_rag_tools.py`

**Interfaces:**
- Consumes: `create_provisioning_token`/`list_provisioning_tokens`（`app/routers/provisioning_tokens.py`）、`create_alert_rule`/`list_alert_rules`（`app/routers/alert_rules.py`）、`put_panel_configs`/`get_panel_configs`（`app/routers/tenant_portal.py`）、`get_tenant_stats`（`app/routers/stats.py`）、`get_tenant_invoice`（`app/routers/billing.py`）、`Tenant`一覧（`app/models/public.py`）
- Produces: `AgentTool`データクラス、`TOOLS: list[AgentTool]`（`app/services/rag_tools/__init__.py`。Task 6が使う）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_rag_tools.py`を新規作成:

```python
from unittest.mock import MagicMock, patch

from app.services.rag_tools import TOOLS


def test_all_tools_have_required_fields():
    for tool in TOOLS:
        assert tool.name
        assert tool.description
        assert isinstance(tool.input_schema, dict)
        assert isinstance(tool.read_only, bool)
        assert callable(tool.handler)


def test_tool_names_are_unique():
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))


def test_action_tools_are_marked_not_read_only():
    action_tool_names = {"provisioning_token_issue", "dashboard_panel_config_set", "alert_rule_create"}
    for tool in TOOLS:
        if tool.name in action_tool_names:
            assert tool.read_only is False


def test_read_only_tools_are_marked_read_only():
    read_only_names = {
        "tenant_list", "tenant_stats_get", "tenant_invoice_get",
        "provisioning_token_list", "dashboard_panel_config_list", "alert_rule_list",
    }
    for tool in TOOLS:
        if tool.name in read_only_names:
            assert tool.read_only is True


def test_provisioning_token_issue_calls_existing_endpoint():
    from app.services.rag_tools.provisioning import issue_provisioning_token

    with patch("app.services.rag_tools.provisioning.create_provisioning_token") as mock_create:
        mock_create.return_value = MagicMock(id="token-1")
        issue_provisioning_token(tenant_id="tenant-1", max_devices=10, expires_days=365)

    mock_create.assert_called_once()
    call_args = mock_create.call_args
    assert call_args.kwargs["tenant_id"] == "tenant-1"
    assert call_args.kwargs["body"].max_devices == 10
    assert call_args.kwargs["payload"]["role"] is None or call_args.kwargs["payload"].get("type") == "platform"


def test_alert_rule_create_calls_existing_endpoint():
    from app.services.rag_tools.alerts import create_alert_rule_tool

    with patch("app.services.rag_tools.alerts.create_alert_rule") as mock_create:
        create_alert_rule_tool(
            tenant_id="tenant-1", sensor_key="temperature", condition="above", threshold=80.0,
        )

    mock_create.assert_called_once()
    call_args = mock_create.call_args
    assert call_args.kwargs["tenant_id"] == "tenant-1"
    assert call_args.kwargs["body"].sensor_key == "temperature"
    assert call_args.kwargs["body"].condition == "above"
    assert call_args.kwargs["body"].threshold == 80.0


def test_dashboard_panel_config_set_calls_existing_endpoint_with_synthetic_tenant_payload():
    from app.services.rag_tools.dashboard import set_dashboard_panel_config

    with patch("app.services.rag_tools.dashboard.put_panel_configs") as mock_put:
        set_dashboard_panel_config(tenant_id="tenant-1", sensor_key="temperature", panel_type="timeseries")

    mock_put.assert_called_once()
    call_args = mock_put.call_args
    assert call_args.kwargs["payload"]["tenant_id"] == "tenant-1"
    assert call_args.kwargs["payload"]["role"] == "admin"
    assert call_args.kwargs["items"][0].sensor_key == "temperature"
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_rag_tools.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: `rag_tools/__init__.py`を新規作成する**

`core-api/app/services/rag_tools/__init__.py`:

```python
from dataclasses import dataclass
from typing import Callable


@dataclass
class AgentTool:
    name: str
    description: str
    input_schema: dict
    read_only: bool
    handler: Callable


from . import alerts, dashboard, general, provisioning  # noqa: E402

TOOLS: list[AgentTool] = [
    *general.TOOLS,
    *provisioning.TOOLS,
    *dashboard.TOOLS,
    *alerts.TOOLS,
]
```

- [ ] **Step 4: `rag_tools/provisioning.py`を新規作成する**

既存の`create_provisioning_token(tenant_id: str, body: TokenCreate, payload: dict)`（`app/routers/provisioning_tokens.py`）・`list_provisioning_tokens(tenant_id: str, _: dict)`（同ファイル）をラップする:

```python
from app.routers.provisioning_tokens import (
    TokenCreate,
    create_provisioning_token,
    list_provisioning_tokens,
)
from app.services.rag_tools import AgentTool

_SYSTEM_PAYLOAD = {"sub": "00000000-0000-0000-0000-000000000000", "email": "assistant@platform", "type": "platform"}


def issue_provisioning_token(tenant_id: str, max_devices: int | None = None, expires_days: int | None = None) -> dict:
    token = create_provisioning_token(
        tenant_id=tenant_id,
        body=TokenCreate(max_devices=max_devices, expires_days=expires_days),
        payload=_SYSTEM_PAYLOAD,
    )
    return token.model_dump() if hasattr(token, "model_dump") else dict(token)


def list_tenant_provisioning_tokens(tenant_id: str) -> list[dict]:
    tokens = list_provisioning_tokens(tenant_id=tenant_id, _=_SYSTEM_PAYLOAD)
    return [t.model_dump() if hasattr(t, "model_dump") else dict(t) for t in tokens]


TOOLS = [
    AgentTool(
        name="provisioning_token_issue",
        description="テナントの新しいプロビジョニングトークンを発行する。デバイスをプラットフォームに登録するために必要。",
        input_schema={
            "type": "object",
            "properties": {
                "max_devices": {"type": "integer", "description": "登録可能な最大デバイス数（省略時は100）"},
                "expires_days": {"type": "integer", "description": "有効期限（日数、省略時は365日）"},
            },
        },
        read_only=False,
        handler=issue_provisioning_token,
    ),
    AgentTool(
        name="provisioning_token_list",
        description="テナントの発行済みプロビジョニングトークン一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_tenant_provisioning_tokens,
    ),
]
```

- [ ] **Step 5: `rag_tools/alerts.py`を新規作成する**

既存の`create_alert_rule(tenant_id: str, body: AlertRuleCreate, _: dict)`・`list_alert_rules(tenant_id: str, _: dict)`（`app/routers/alert_rules.py`）をラップする:

```python
from app.routers.alert_rules import AlertRuleCreate, create_alert_rule, list_alert_rules
from app.services.rag_tools import AgentTool

_SYSTEM_PAYLOAD = {"sub": "00000000-0000-0000-0000-000000000000", "email": "assistant@platform", "type": "platform"}


def create_alert_rule_tool(
    tenant_id: str, sensor_key: str, condition: str, threshold: float | None = None,
    severity: str = "warning", notify_emails: list[str] | None = None,
) -> dict:
    rule = create_alert_rule(
        tenant_id=tenant_id,
        body=AlertRuleCreate(
            sensor_key=sensor_key, condition=condition, threshold=threshold,
            severity=severity, notify_emails=notify_emails or [],
        ),
        _=_SYSTEM_PAYLOAD,
    )
    return rule.model_dump() if hasattr(rule, "model_dump") else dict(rule)


def list_tenant_alert_rules(tenant_id: str) -> list[dict]:
    rules = list_alert_rules(tenant_id=tenant_id, _=_SYSTEM_PAYLOAD)
    return [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rules]


TOOLS = [
    AgentTool(
        name="alert_rule_create",
        description="テナントにアラートルールを作成する。センサー値が条件を満たしたときに通知する。",
        input_schema={
            "type": "object",
            "properties": {
                "sensor_key": {"type": "string", "description": "対象のセンサーキー（例: temperature）"},
                "condition": {"type": "string", "enum": ["above", "below", "equal", "device_offline"]},
                "threshold": {"type": "number", "description": "しきい値（device_offline以外で必須）"},
                "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                "notify_emails": {"type": "array", "items": {"type": "string"}, "description": "通知先メールアドレス"},
            },
            "required": ["sensor_key", "condition"],
        },
        read_only=False,
        handler=create_alert_rule_tool,
    ),
    AgentTool(
        name="alert_rule_list",
        description="テナントの現在のアラートルール一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_tenant_alert_rules,
    ),
]
```

- [ ] **Step 6: `rag_tools/dashboard.py`を新規作成する**

既存の`put_panel_configs`・`get_panel_configs`（`app/routers/tenant_portal.py`）はテナント管理者向けエンドポイントでありPF管理者向けの代替が存在しないため、テナントロール相当のペイロードを合成して直接呼び出す（この分岐が`provisioning`/`alerts`と異なる点。両関数とも監査ログ記録を行っていないため、属性の取り違えリスクは無い）:

```python
from app.routers.tenant_portal import PanelConfigItem, get_panel_configs, put_panel_configs
from app.services.rag_tools import AgentTool


def _tenant_admin_payload(tenant_id: str) -> dict:
    return {"tenant_id": tenant_id, "sub": "assistant", "email": "assistant@platform", "role": "admin", "type": "tenant"}


def set_dashboard_panel_config(tenant_id: str, sensor_key: str, panel_type: str) -> dict:
    put_panel_configs(
        items=[PanelConfigItem(sensor_key=sensor_key, panel_type=panel_type)],
        group_id=None,
        payload=_tenant_admin_payload(tenant_id),
    )
    return {"sensor_key": sensor_key, "panel_type": panel_type}


def list_dashboard_panel_configs(tenant_id: str) -> list[dict]:
    return get_panel_configs(group_id=None, payload=_tenant_admin_payload(tenant_id))


TOOLS = [
    AgentTool(
        name="dashboard_panel_config_set",
        description="テナントのダッシュボードで、指定センサーの表示形式（グラフ種別）を設定する。",
        input_schema={
            "type": "object",
            "properties": {
                "sensor_key": {"type": "string"},
                "panel_type": {
                    "type": "string",
                    "enum": ["timeseries", "barchart", "histogram", "heatmap", "state-timeline", "gauge", "stat", "bargauge", "table"],
                },
            },
            "required": ["sensor_key", "panel_type"],
        },
        read_only=False,
        handler=set_dashboard_panel_config,
    ),
    AgentTool(
        name="dashboard_panel_config_list",
        description="テナントの現在のダッシュボード表示設定一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_dashboard_panel_configs,
    ),
]
```

- [ ] **Step 7: `rag_tools/general.py`を新規作成する**

汎用の読み取り専用ツール（テナント名解決・統計・請求書）。既存の`get_tenant_stats(tenant_id, _)`（`app/routers/stats.py`）・`get_tenant_invoice(tenant_id, target_year_month, _)`（`app/routers/billing.py`）・`Tenant`一覧をラップする:

```python
from app.database import SessionLocal
from app.models.public import Tenant
from app.routers.billing import get_tenant_invoice
from app.routers.stats import get_tenant_stats
from app.services.rag_tools import AgentTool

_SYSTEM_PAYLOAD = {"sub": "00000000-0000-0000-0000-000000000000", "email": "assistant@platform", "type": "platform"}


def list_all_tenants() -> list[dict]:
    with SessionLocal() as db:
        rows = db.query(Tenant).filter(Tenant.status != "deleted").order_by(Tenant.name).all()
        return [{"id": str(t.id), "name": t.name} for t in rows]


def get_tenant_stats_tool(tenant_id: str) -> dict:
    return get_tenant_stats(tenant_id=tenant_id, _=_SYSTEM_PAYLOAD)


def get_tenant_invoice_tool(tenant_id: str, target_year_month: str) -> dict:
    return get_tenant_invoice(tenant_id=tenant_id, target_year_month=target_year_month, _=_SYSTEM_PAYLOAD)


TOOLS = [
    AgentTool(
        name="tenant_list",
        description="全テナントの名前とIDの一覧を取得する。質問文に出てくるテナント名をIDに変換するために使う。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=lambda tenant_id=None: list_all_tenants(),
    ),
    AgentTool(
        name="tenant_stats_get",
        description="テナントの統計情報（デバイス数・データポイント数・アラート件数等）を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=get_tenant_stats_tool,
    ),
    AgentTool(
        name="tenant_invoice_get",
        description="テナントの指定月の請求書明細を取得する。",
        input_schema={
            "type": "object",
            "properties": {"target_year_month": {"type": "string", "description": "対象年月（YYYY-MM形式）"}},
            "required": ["target_year_month"],
        },
        read_only=True,
        handler=get_tenant_invoice_tool,
    ),
]
```

（`tenant_list`は他ツールと違い特定のテナントに紐付かないため、ハンドラは`tenant_id`引数を無視する形にしている。Task 6のオーケストレーション層は全ツールに対して一律`tenant_id`を渡す設計にするため、シグネチャを揃える。）

- [ ] **Step 8: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_rag_tools.py -v`
Expected: PASS（全7件）

- [ ] **Step 9: コミット**

```bash
git add core-api/app/services/rag_tools/ core-api/tests/test_rag_tools.py
git commit -m "feat: エージェント用ツールレジストリとPhase1の3ドメイン(ブートストラップ/可視化/アラート)を追加"
```

---

### Task 6: チャットオーケストレーション（ベクトル検索＋ツール呼び出しループ＋確認フロー）

**Files:**
- Create: `core-api/app/services/rag.py`
- Test: `core-api/tests/test_rag.py`

**Interfaces:**
- Consumes: `embed`, `chat`（Task 2）、`TOOLS`（Task 5）、`doc_chunks`/`agent_pending_actions`テーブル（Task 1）
- Produces:
  - `answer_question(db: Session, tenant_id: str, message: str, requested_by: str) -> dict`（`{"answer": str, "sources": list[dict], "pending_action": dict | None}`を返す。Task 7が使う）
  - `execute_pending_action(db: Session, tenant_id: str, pending_action_id: str) -> dict`（実行結果を返す。Task 7が使う）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_rag.py`を新規作成:

```python
from unittest.mock import MagicMock, patch

from app.services.rag import answer_question, execute_pending_action


def _fake_chunk(source_path="docs/design.html", heading="見出し", content="本文", distance=0.1):
    row = MagicMock()
    row.source_path = source_path
    row.heading = heading
    row.content = content
    row.distance = distance
    return row


def test_answer_question_returns_direct_answer_when_no_tool_call():
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = [_fake_chunk()]

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", return_value={"role": "assistant", "content": "回答です", "tool_calls": None}):
        result = answer_question(mock_db, tenant_id="tenant-1", message="質問です", requested_by="admin@example.com")

    assert result["answer"] == "回答です"
    assert result["sources"] == [{"source_path": "docs/design.html", "heading": "見出し"}]
    assert result["pending_action"] is None


def test_answer_question_executes_read_only_tool_and_continues():
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []

    tool_call = {"id": "call-1", "function": {"name": "tenant_stats_get", "arguments": "{}"}}
    responses = [
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "assistant", "content": "統計を踏まえた回答です", "tool_calls": None},
    ]

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", side_effect=responses), \
         patch("app.services.rag.TOOLS") as mock_tools:
        read_tool = MagicMock(name="tenant_stats_get", read_only=True)
        read_tool.name = "tenant_stats_get"
        read_tool.handler = MagicMock(return_value={"device_count": 5})
        mock_tools.__iter__.return_value = iter([read_tool])

        result = answer_question(mock_db, tenant_id="tenant-1", message="統計は？", requested_by="admin@example.com")

    assert result["answer"] == "統計を踏まえた回答です"
    assert result["pending_action"] is None
    read_tool.handler.assert_called_once_with(tenant_id="tenant-1")


def test_answer_question_creates_pending_action_for_action_tool():
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []

    tool_call = {"id": "call-1", "function": {"name": "alert_rule_create", "arguments": '{"sensor_key": "temperature", "condition": "above", "threshold": 80}'}}

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", return_value={"role": "assistant", "content": None, "tool_calls": [tool_call]}), \
         patch("app.services.rag.TOOLS") as mock_tools:
        action_tool = MagicMock(read_only=False)
        action_tool.name = "alert_rule_create"
        action_tool.description = "アラートルールを作成する"
        mock_tools.__iter__.return_value = iter([action_tool])

        result = answer_question(mock_db, tenant_id="tenant-1", message="アラート作って", requested_by="admin@example.com")

    assert result["pending_action"] is not None
    assert result["pending_action"]["tool_name"] == "alert_rule_create"
    assert mock_db.add.called
    added = mock_db.add.call_args[0][0]
    assert added.tenant_id == "tenant-1"
    assert added.tool_name == "alert_rule_create"
    assert added.requested_by == "admin@example.com"
    mock_db.commit.assert_called_once()


def test_execute_pending_action_runs_handler_and_deletes_record():
    pending = MagicMock()
    pending.id = "pending-1"
    pending.tenant_id = "tenant-1"
    pending.tool_name = "alert_rule_create"
    pending.tool_args = {"sensor_key": "temperature", "condition": "above", "threshold": 80}
    pending.expires_at.__gt__ = lambda self, other: True

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = pending

    with patch("app.services.rag.TOOLS") as mock_tools, \
         patch("app.services.rag.write_audit_log") as mock_audit:
        action_tool = MagicMock()
        action_tool.name = "alert_rule_create"
        action_tool.handler = MagicMock(return_value={"id": "rule-1"})
        mock_tools.__iter__.return_value = iter([action_tool])

        result = execute_pending_action(mock_db, tenant_id="tenant-1", pending_action_id="pending-1")

    action_tool.handler.assert_called_once_with(tenant_id="tenant-1", sensor_key="temperature", condition="above", threshold=80)
    assert result == {"id": "rule-1"}
    mock_db.delete.assert_called_once_with(pending)
    assert mock_audit.called


def test_execute_pending_action_returns_none_when_not_found():
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    result = execute_pending_action(mock_db, tenant_id="tenant-1", pending_action_id="missing")

    assert result is None
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_rag.py -v`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: `rag.py`を新規作成する**

`core-api/app/services/rag.py`:

```python
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.rag import AgentPendingAction
from app.services.audit import write_audit_log
from app.services.ollama_client import chat, embed
from app.services.rag_tools import TOOLS

_MAX_TOOL_ROUNDS = 2
_TOP_K_CHUNKS = 5
_PENDING_ACTION_TTL_MINUTES = 10

_SYSTEM_PROMPT = (
    "あなたはIoTプラットフォームの操作・仕様に関する質問に答えるアシスタントです。"
    "提供されたドキュメントの抜粋を参考に、日本語で簡潔に回答してください。"
    "プラットフォームの現在の情報が必要な場合はツールを使ってください。"
    "ユーザーが操作の実行（トークン発行・設定変更・アラート作成等）を依頼した場合は、"
    "該当するツールを呼び出してください（実際の実行はユーザーの確認後に行われます）。"
)


def _search_chunks(db: Session, question_embedding: list[float]) -> list:
    embedding_str = "[" + ",".join(str(x) for x in question_embedding) + "]"
    result = db.execute(
        text("""
            SELECT source_path, heading, content, embedding <=> :query_embedding AS distance
            FROM doc_chunks
            ORDER BY distance
            LIMIT :top_k
        """),
        {"query_embedding": embedding_str, "top_k": _TOP_K_CHUNKS},
    )
    return result.fetchall()


def _tool_schemas() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in TOOLS
    ]


def _find_tool(name: str):
    for t in TOOLS:
        if t.name == name:
            return t
    return None


def answer_question(db: Session, tenant_id: str, message: str, requested_by: str) -> dict:
    """質問に回答する。読み取り専用ツールは即実行し、アクション実行ツールは
    pending_actionとして保存し確認待ちにする。
    戻り値: {"answer": str, "sources": list[dict], "pending_action": dict | None}"""
    question_embedding = embed(message)
    chunks = _search_chunks(db, question_embedding)

    context_text = "\n\n".join(
        f"[出典: {c.source_path} - {c.heading or '(見出しなし)'}]\n{c.content}" for c in chunks
    )
    sources = [{"source_path": c.source_path, "heading": c.heading} for c in chunks]

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"参考ドキュメント:\n{context_text}\n\n質問: {message}"},
    ]

    for _ in range(_MAX_TOOL_ROUNDS):
        response = chat(messages=messages, tools=_tool_schemas())

        if not response.get("tool_calls"):
            return {"answer": response.get("content") or "", "sources": sources, "pending_action": None}

        tool_call = response["tool_calls"][0]
        tool_name = tool_call["function"]["name"]
        tool_args = json.loads(tool_call["function"]["arguments"] or "{}")
        tool = _find_tool(tool_name)

        if tool is None:
            messages.append({"role": "assistant", "content": None, "tool_calls": response["tool_calls"]})
            messages.append({"role": "tool", "content": json.dumps({"error": "unknown tool"})})
            continue

        if tool.read_only:
            result = tool.handler(tenant_id=tenant_id, **tool_args)
            messages.append({"role": "assistant", "content": None, "tool_calls": response["tool_calls"]})
            messages.append({"role": "tool", "content": json.dumps(result, default=str)})
            continue

        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_PENDING_ACTION_TTL_MINUTES)
        pending = AgentPendingAction(
            tenant_id=tenant_id, tool_name=tool_name, tool_args=tool_args,
            requested_by=requested_by, expires_at=expires_at,
        )
        db.add(pending)
        db.commit()
        db.refresh(pending)
        return {
            "answer": f"「{tool.description}」を実行しますか？",
            "sources": sources,
            "pending_action": {
                "pending_action_id": str(pending.id),
                "tool_name": tool_name,
                "tool_args": tool_args,
            },
        }

    return {"answer": response.get("content") or "", "sources": sources, "pending_action": None}


def execute_pending_action(db: Session, tenant_id: str, pending_action_id: str):
    """未確認アクションを実行する。見つからない/期限切れならNoneを返す。"""
    pending = db.query(AgentPendingAction).filter(
        AgentPendingAction.id == pending_action_id,
        AgentPendingAction.tenant_id == tenant_id,
    ).first()
    if pending is None:
        return None
    if pending.expires_at < datetime.now(timezone.utc):
        db.delete(pending)
        db.commit()
        return None

    tool = _find_tool(pending.tool_name)
    result = tool.handler(tenant_id=tenant_id, **pending.tool_args)

    write_audit_log(
        db, "platform", "00000000-0000-0000-0000-000000000000", pending.requested_by,
        f"ai_assistant_{pending.tool_name}",
        tenant_id=tenant_id, resource_type="ai_assistant_action",
        detail={"via": "ai_assistant", "confirmed_by": pending.requested_by, "tool_args": pending.tool_args},
    )
    db.delete(pending)
    db.commit()
    return result
```

（`write_audit_log`の実シグネチャは`write_audit_log(db, actor_type, actor_id, actor_email, action, *, tenant_id=None, resource_type=None, resource_id=None, detail=None, ...)`——`app/services/audit.py`参照。`actor_id`はUUID文字列である必要がある。上記コードは位置引数でこの順序通りに渡している。）

`core-api/app/models/rag.py`に`AgentPendingAction`モデルを追記（Task 4で作成済みのファイルに追記する）:

```python


class AgentPendingAction(Base):
    __tablename__ = "agent_pending_actions"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(UUID(as_uuid=True), nullable=False)
    tool_name = Column(String(100), nullable=False)
    tool_args = Column(JSONB, nullable=False)
    requested_by = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
```

`core-api/app/models/rag.py`先頭のimport行:

```python
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
```

を以下に置き換え:

```python
from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_rag.py -v`
Expected: PASS（全5件）

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/rag.py core-api/app/models/rag.py core-api/tests/test_rag.py
git commit -m "feat: RAGチャットオーケストレーション(ベクトル検索+ツール呼び出し+確認フロー)を追加"
```

---

### Task 7: API（チャット・確認実行・再インデックス）

**Files:**
- Create: `core-api/app/routers/rag.py`
- Modify: `core-api/app/main.py`（ルーター登録）
- Test: `core-api/tests/test_rag_api.py`

**Interfaces:**
- Consumes: `answer_question`, `execute_pending_action`（Task 6）、`reindex_all_documents`（Task 4）
- Produces: `POST /tenants/{tenant_id}/assistant/chat`, `POST /tenants/{tenant_id}/assistant/confirm-action`, `POST /platform/assistant/reindex`（仕様書§7の通り3エンドポイント全てを`app/routers/rag.py`に集約する）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_rag_api.py`を新規作成:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_chat_requires_platform_auth():
    resp = client.post(f"/tenants/{TENANT_ID}/assistant/chat", json={"message": "質問"})
    assert resp.status_code == 401


def test_chat_returns_answer():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.answer_question", return_value={"answer": "回答です", "sources": [], "pending_action": None}) as mock_answer:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/chat",
            json={"message": "質問"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"answer": "回答です", "sources": [], "pending_action": None}
    mock_answer.assert_called_once()
    assert mock_answer.call_args.kwargs["tenant_id"] == TENANT_ID
    assert mock_answer.call_args.kwargs["message"] == "質問"
    assert mock_answer.call_args.kwargs["requested_by"] == "admin@iot.local"


def test_confirm_action_requires_platform_auth():
    resp = client.post(f"/tenants/{TENANT_ID}/assistant/confirm-action", json={"pending_action_id": "x"})
    assert resp.status_code == 401


def test_confirm_action_executes_and_returns_result():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.execute_pending_action", return_value={"id": "rule-1"}) as mock_execute:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/confirm-action",
            json={"pending_action_id": "pending-1"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"result": {"id": "rule-1"}}
    mock_execute.assert_called_once()


def test_confirm_action_returns_404_when_not_found():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.execute_pending_action", return_value=None):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            f"/tenants/{TENANT_ID}/assistant/confirm-action",
            json={"pending_action_id": "missing"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_reindex_documents_requires_platform_auth():
    resp = client.post("/platform/assistant/reindex")
    assert resp.status_code == 401


def test_reindex_documents_returns_chunk_count():
    with patch("app.routers.rag.SessionLocal") as mock_session, \
         patch("app.routers.rag.reindex_all_documents", return_value=42) as mock_reindex:
        mock_db = _session_ctx()
        mock_session.return_value = mock_db
        resp = client.post(
            "/platform/assistant/reindex",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"chunk_count": 42}
    mock_reindex.assert_called_once_with(mock_db)
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_rag_api.py -v`
Expected: FAIL（404、対応するルートが無い）

- [ ] **Step 3: `rag.py`ルーターを新規作成する**

`core-api/app/routers/rag.py`:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from app.database import SessionLocal
from app.services.auth import verify_token
from app.services.rag import answer_question, execute_pending_action
from app.services.rag_indexing import reindex_all_documents

router = APIRouter(tags=["assistant"])
_bearer = HTTPBearer(auto_error=False)


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _parse_uuid(value: str, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}")


class ChatMessage(BaseModel):
    message: str


class ConfirmActionBody(BaseModel):
    pending_action_id: str


@router.post("/tenants/{tenant_id}/assistant/chat")
def chat_with_assistant(tenant_id: str, body: ChatMessage, payload: dict = Depends(_require_platform)):
    tenant_uuid = _parse_uuid(tenant_id, "tenant_id")
    with SessionLocal() as db:
        return answer_question(db, tenant_id=str(tenant_uuid), message=body.message, requested_by=payload["email"])


@router.post("/tenants/{tenant_id}/assistant/confirm-action")
def confirm_assistant_action(tenant_id: str, body: ConfirmActionBody, payload: dict = Depends(_require_platform)):
    tenant_uuid = _parse_uuid(tenant_id, "tenant_id")
    with SessionLocal() as db:
        result = execute_pending_action(db, tenant_id=str(tenant_uuid), pending_action_id=body.pending_action_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending action not found or expired")
    return {"result": result}


@router.post("/platform/assistant/reindex")
def reindex_documents(_: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        count = reindex_all_documents(db)
    return {"chunk_count": count}
```

（このファイル内でも既存の`provisioning_tokens.py`等と同じ`_require_platform`/`_parse_uuid`をこのファイル用に再定義する。これは共有ユーティリティ化されておらず各ルーターファイルが個別に定義する、既存コードベースの一貫した流儀に従うもの。）

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_rag_api.py -v`
Expected: PASS（全7件）

- [ ] **Step 5: `main.py`に配線する**

`core-api/app/main.py`の以下の行:

```python
from app.routers import health, auth, mfa, tenants, provisioning, emqx, provisioning_tokens, alert_rules, emqx_events, firmware, stats, tenant_auth, tenant_mfa, tenant_users, tenant_devices, tenant_grafana, tenant_portal, public_access, platform, audit_logs, device_groups, billing
```

を以下に置き換え:

```python
from app.routers import health, auth, mfa, tenants, provisioning, emqx, provisioning_tokens, alert_rules, emqx_events, firmware, stats, tenant_auth, tenant_mfa, tenant_users, tenant_devices, tenant_grafana, tenant_portal, public_access, platform, audit_logs, device_groups, billing, rag
```

以下の行:

```python
app.include_router(billing.router)
```

を以下に置き換え:

```python
app.include_router(billing.router)
app.include_router(rag.router)
```

- [ ] **Step 6: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_rag_api.py -v`
Expected: PASS（全件、変更なしのはずだが念のため再確認）

- [ ] **Step 7: コミット**

```bash
git add core-api/app/routers/rag.py core-api/app/main.py core-api/tests/test_rag_api.py
git commit -m "feat: AIアシスタントのチャット・アクション確認実行APIを追加"
```

---

### Task 8: UI（チャット画面・再インデックスボタン）

**Files:**
- Modify: `platform-ui/tenant.html`（新規タブ「AIアシスタント」）
- Modify: `platform-ui/platform-settings.html`（再インデックスカード）
- Modify: `admin-ui/static/tailwind.css`（`npm run build:css`で再生成）

このタスクは自動テストなし（既存のUI機能と同様、手動確認）。

- [ ] **Step 1: `platform-ui/tenant.html`に新規タブを追加する**

タブ一覧に定義がある箇所（既存の「単価設定」タブボタンの行、`activeTab = 'billing'; loadBillingPrices()`のあたり）の直後に、新規タブボタンを追加する:

```html
        <button @click="activeTab = 'assistant'"
                :class="activeTab === 'assistant' ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'"
                class="px-4 py-2 text-sm font-medium">AIアシスタント</button>
```

（既存タブボタン一覧の末尾に追加する。正確な挿入位置は実装時にファイルを読んで既存のタブボタン群の並びを確認すること。）

タブコンテンツ領域（既存の`活動タブ === 'billing'`の`<div>`ブロックの後）に、新規タブのコンテンツを追加する:

```html
      <!-- AIアシスタントタブ -->
      <div x-show="activeTab === 'assistant'">
        <h3 class="font-medium text-gray-700 mb-4">AIアシスタント</h3>
        <div class="bg-white rounded-xl shadow-sm border border-gray-200 p-4" style="height: 500px; display: flex; flex-direction: column;">
          <div class="flex-1 overflow-y-auto space-y-3 mb-3" x-ref="chatLog">
            <template x-for="(msg, idx) in assistantMessages" :key="idx">
              <div :class="msg.role === 'user' ? 'text-right' : 'text-left'">
                <template x-if="msg.role !== 'pending_action'">
                  <div :class="msg.role === 'user' ? 'inline-block bg-blue-600 text-white rounded-lg px-3 py-2 text-sm' : 'inline-block bg-gray-100 text-gray-800 rounded-lg px-3 py-2 text-sm'">
                    <p x-text="msg.content"></p>
                    <template x-if="msg.sources && msg.sources.length > 0">
                      <p class="text-xs text-gray-400 mt-1">
                        出典: <template x-for="(s, i) in msg.sources" :key="i"><span x-text="s.source_path + (s.heading ? ' - ' + s.heading : '') + (i < msg.sources.length - 1 ? '、' : '')"></span></template>
                      </p>
                    </template>
                  </div>
                </template>
                <template x-if="msg.role === 'pending_action'">
                  <div class="inline-block bg-yellow-50 border border-yellow-300 rounded-lg px-3 py-2 text-sm">
                    <p x-text="msg.content"></p>
                    <div class="mt-2 flex gap-2">
                      <button @click="confirmAssistantAction(msg.pending_action_id)" class="bg-blue-600 text-white text-xs px-3 py-1 rounded">実行する</button>
                      <button @click="msg.dismissed = true" x-show="!msg.dismissed" class="text-gray-500 text-xs px-3 py-1">キャンセル</button>
                    </div>
                  </div>
                </template>
              </div>
            </template>
            <div x-show="assistantLoading" class="text-left text-xs text-gray-400">考え中...</div>
          </div>
          <form @submit.prevent="sendAssistantMessage()" class="flex gap-2">
            <input type="text" x-model="assistantInput" placeholder="質問を入力..."
                   class="flex-1 border border-gray-300 rounded px-3 py-2 text-sm" :disabled="assistantLoading">
            <button type="submit" class="bg-blue-600 text-white px-4 py-2 rounded text-sm" :disabled="assistantLoading || !assistantInput">送信</button>
          </form>
        </div>
      </div>
```

`invoices: [], invoicesLoading: false,`の行の直前に、新規状態を追記する:

```javascript
        assistantMessages: [], assistantInput: '', assistantLoading: false,
```

`saveRetentionDays()`メソッドの後に、新規メソッドを追記する:

```javascript
        async sendAssistantMessage() {
          const question = this.assistantInput;
          if (!question) return;
          this.assistantMessages.push({ role: 'user', content: question });
          this.assistantInput = '';
          this.assistantLoading = true;
          try {
            const result = await api.request('POST', `/tenants/${tenantId}/assistant/chat`, { message: question });
            this.assistantMessages.push({ role: 'assistant', content: result.answer, sources: result.sources });
            if (result.pending_action) {
              this.assistantMessages.push({
                role: 'pending_action', content: result.answer,
                pending_action_id: result.pending_action.pending_action_id, dismissed: false,
              });
            }
          } catch(e) {
            this.assistantMessages.push({ role: 'assistant', content: 'エラー: ' + e.message });
          } finally {
            this.assistantLoading = false;
          }
        },
        async confirmAssistantAction(pendingActionId) {
          this.assistantLoading = true;
          try {
            const result = await api.request('POST', `/tenants/${tenantId}/assistant/confirm-action`, { pending_action_id: pendingActionId });
            this.assistantMessages.push({ role: 'assistant', content: '実行しました。' });
          } catch(e) {
            this.assistantMessages.push({ role: 'assistant', content: 'エラー: ' + e.message });
          } finally {
            this.assistantLoading = false;
          }
        },
```

- [ ] **Step 2: `platform-ui/platform-settings.html`に再インデックスカードを追加する**

「データ保持期間（デフォルト）」カードの後に、新規カードを追加する:

```html

    <div x-show="!loading" class="bg-white rounded-xl shadow-sm border border-gray-200 p-6 mt-6">
      <h2 class="text-base font-semibold text-gray-800 mb-1">AIアシスタント ドキュメント再インデックス</h2>
      <p class="text-xs text-gray-400 mb-4">design.html・specs・CLAUDE.md等のドキュメントをAIアシスタントの検索対象として再取り込みします。ドキュメントを更新した後に実行してください。</p>
      <div x-show="reindexError" class="mb-3 text-sm text-red-600" x-text="reindexError"></div>
      <button @click="reindexDocuments()" :disabled="reindexing"
              class="bg-blue-600 hover:bg-blue-700 disabled:bg-blue-300 text-white px-4 py-2 rounded text-sm">
        <span x-show="!reindexing">再インデックスを実行</span>
        <span x-show="reindexing">実行中...</span>
      </button>
      <p x-show="reindexResult" class="mt-3 text-sm text-green-600" x-text="reindexResult"></p>
    </div>
```

`retentionLoadFailed: false,`の行の直後に追記する:

```javascript
        reindexing: false, reindexError: '', reindexResult: '',
```

`saveRetentionDays()`メソッドの後に、新規メソッドを追記する:

```javascript
        async reindexDocuments() {
          this.reindexError = '';
          this.reindexResult = '';
          this.reindexing = true;
          try {
            const result = await api.request('POST', '/platform/assistant/reindex');
            this.reindexResult = `${result.chunk_count}件のチャンクを再インデックスしました`;
          } catch(e) { this.reindexError = e.message; }
          finally { this.reindexing = false; }
        },
```

（`api.request`は`admin-ui/js/api.js`のジェネリック呼び出し関数。既にこのファイルで使われている前提——使われていなければ`api.platform.*`のような専用関数を追加する必要があるので、実装時に`platform-settings.html`が現在ジェネリック`api.request`を直接呼んでいるか確認すること。呼んでいなければ`admin-ui/js/api.js`の`platform`名前空間に`reindexAssistantDocs: () => request('POST', '/platform/assistant/reindex')`を追加しそちらを使う。）

- [ ] **Step 3: HTMLタグの整合性を確認する**

Run:
```bash
python3 -c "
import re
for path in ['platform-ui/tenant.html', 'platform-ui/platform-settings.html']:
    content = open(path, encoding='utf-8').read()
    for tag in ['div','button','template','form']:
        opens = len(re.findall(r'<'+tag+r'[\s>]', content)) - len(re.findall(r'<'+tag+r'[^>]*/>', content))
        closes = len(re.findall(r'</'+tag+r'>', content))
        print(path, tag, opens, closes)
"
```
Expected: 各`tag`について`opens == closes`

- [ ] **Step 4: Tailwind CSSをビルドする**

Run: `npm run build:css`（リポジトリルートから実行）
Expected: `Done in ...ms.`（エラーなし）

- [ ] **Step 5: 手動UI確認**

Ollamaのモデルpull（Task 1注記）を実施した上で開発環境を起動し、以下を確認する:
1. `platform-ui/tenant.html`の「AIアシスタント」タブでドキュメントに関する質問（例:「無制限プロビジョニングトークンとは？」）に回答が返ること。
2. 「アラートルールを作りたい」等、アクション実行を伴う依頼に対して確認カードが表示され、「実行する」で実際にアラートルールが作成されること。
3. `platform-ui/platform-settings.html`の再インデックスボタンでチャンク数が表示されること。
4. ブラウザのコンソールにJSエラーが出ていないこと。

（このステップは自動テストではなく手動確認。CPU推論のため応答に数十秒かかる場合がある。実施できない場合はその旨を明示的に報告する。）

- [ ] **Step 6: コミット**

```bash
git add platform-ui/tenant.html platform-ui/platform-settings.html admin-ui/static/tailwind.css
git commit -m "feat: AIアシスタントのチャットUIと再インデックスUIを追加"
```

---

### Task 9: 最終確認

**Files:** なし（既存ファイルの検証のみ）

- [ ] **Step 1: core-api全体のテストスイートを実行する**

Run: `cd core-api && python -m pytest tests/ -v`
Expected: 既存の無関係なベースライン失敗（`test_auth.py::test_hash_and_verify_password`等、bcryptバージョン起因の既知の7件）を除き、全件PASS。

- [ ] **Step 2: `docker compose config`で構文検証する**

Run: `docker compose config --quiet`
Expected: エラーなし

- [ ] **Step 3: 完了報告**

全タスクの完了、およびTask 1注記のOllamaモデルpullが未実施であればその旨をユーザーに報告し、pushしてよいか確認する。
