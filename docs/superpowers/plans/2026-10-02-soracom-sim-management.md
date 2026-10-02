# SORACOM回線管理機能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナントが自分のSORACOM契約の回線（SIM）を、プラットフォームの管理画面から確認・操作（有効化/停止・速度クラス変更）し、SIM（IMSI）とデバイス（device_id）を紐づけて管理できるようにする。

**Architecture:** `routers/tenant_sim.py` → `services/sim_service.py`（中間層。将来のキャッシュ切替に備える）→ `services/soracom_client.py`（SORACOM API呼び出し・認証トークンキャッシュ）、および `services/soracom_credentials.py`（テナントごとの認証情報の暗号化保存・取得）→ `services/crypto.py`（汎用Fernet暗号化）。紐づけ情報はテナントスキーマの新規テーブル`sim_bindings`に、SORACOM認証情報はpublicスキーマの新規テーブル`tenant_soracom_credentials`に保存する。

**Tech Stack:** FastAPI, SQLAlchemy(`text()`による生SQL), httpx, `cryptography`(Fernet, 既存依存), Alpine.js(既存の`tenant-portal.html`タブ構成に新タブを追加)。

**Spec:** `docs/superpowers/specs/2026-10-02-soracom-sim-management-design.md`

## Global Constraints

- 認証情報（Auth Key）はDBに暗号化して保存する。平文保存は不可。
- カバレッジは既定`jp`（`https://api.soracom.io/v1`）、テナントごとに`g`（`https://g.api.soracom.io/v1`）も選べる。
- 回線一覧・状態は都度SORACOM APIに問い合わせる（キャッシュしない）。ルーター層とSORACOM呼び出し層の間に`sim_service.py`を挟み、将来の切替に備える。
- 一覧取得は全件取得をしない。SORACOMのカーソル方式ページネーション（`last_evaluated_key`）をそのまま使う。
- スコープ外: 回線の解約（terminate）、休止（suspend）・standby切替、信号強度の取得（SORACOM APIに存在しない）、SMS送信、データ容量バンドル変更、BootstrapTokenの暗号化。
- 権限: SORACOM認証情報の保存・削除は**admin限定**。回線の確認・操作・紐づけは**admin/operator**。
- 操作（認証情報保存/削除、activate/deactivate/speed-class変更、bind/unbind）は`audit_logs`に記録する。Auth Key本体・SORACOM APIトークンは記録しない。
- エラーメッセージ・ログに、認証情報やSORACOM APIトークンを含めない。
- **仕様からの変更点（実装計画作成時の判断、ユーザー承認不要の裁定）:** 仕様書は新規ページ`admin-ui/tenant-sim.html`を想定していたが、`admin-ui/tenant-portal.html`は既存の全機能（デバイス・ファームウェア・アラート等）を`activeTab`で切り替える単一ページのタブ構成であり、別ページに分離するとサイドバー・ヘッダーのナビゲーションUXが崩れる。既存のタブ構成に合わせ、`tenant-portal.html`へ新規タブ「SIM管理」として追加する（Task 13・14）。

## Review Focus

- SORACOM側のトークンが想定より早く失効し、一覧取得の途中で401が返る → 1回だけ再認証してリトライし、利用者には成功扱いで返す（Task 3の`_request`のテストで固定）。
- 同じIMSI、または同じdevice_idへの紐づけが同時に2件来る（二重クリック等） → DBのUNIQUE制約違反を409に変換する（Task 5の`bind_line`のテストで固定）。
- SORACOM認証情報を削除した直後に、一覧・操作系APIを呼ぶ → 500にならず「連携が設定されていません」の400を返す（Task 7・9のテストで固定）。
- SIMを1件も契約していない（または認証情報未設定の）テナントが一覧を開く → 空配列・`next_cursor: null`を返す、またはNotConfiguredの400を返す。例外にしない（Task 6・9のテストで固定）。
- operatorロールが認証情報の保存・削除を試みる → 403（admin限定の`_require_admin`依存をPUT/DELETEにだけ付ける。GET/line系は`_require_admin_or_operator`）（Task 8のテストで固定）。
- デバイス削除時にSIMが紐づいていた → `sim_bindings`の該当行も削除され、孤立したバインディングが残らない（Task 11のテストで固定）。

---

## File Structure

**新規ファイル:**
- `core-api/app/services/crypto.py` — 汎用Fernet暗号化（`encrypt_secret`/`decrypt_secret`）。
- `core-api/app/services/soracom_credentials.py` — `tenant_soracom_credentials`のCRUD（保存時に暗号化、取得時に復号）。
- `core-api/app/services/soracom_client.py` — SORACOM API呼び出し本体（認証キャッシュ、一覧/詳細/activate/deactivate/update_speed_class、エラー日本語化）。
- `core-api/app/services/sim_service.py` — ルーターとSORACOM呼び出しの中間層。`sim_bindings`とのJOIN、紐づけCRUD、将来のキャッシュ切替の差し替え点。
- `core-api/app/routers/tenant_sim.py` — `/tenant-portal/me/sim/...`のエンドポイント。
- `core-api/tests/test_crypto.py`
- `core-api/tests/test_soracom_credentials.py`
- `core-api/tests/test_soracom_client.py`
- `core-api/tests/test_sim_service.py`
- `core-api/tests/test_tenant_sim_api.py`

**変更ファイル:**
- `core-api/app/config.py` — `secrets_encryption_key`追加。
- `core-api/app/database.py` — `migrate_create_tenant_soracom_credentials()`、`ensure_sim_tables_to_tenant_schema()`追加。
- `core-api/app/main.py` — マイグレーション登録・ルーター登録。
- `core-api/app/routers/tenant_portal.py` — `delete_device`に`sim_bindings`削除を追加。`list_devices`に紐づけ情報を追加。
- `core-api/app/routers/tenant_devices.py` — `delete_tenant_device`に`sim_bindings`削除を追加。
- `core-api/app/routers/tenants.py` / `core-api/app/schemas/tenant.py` — `GET /tenants/{id}`に`soracom_configured`を追加。
- `core-api/tests/conftest.py` — `SECRETS_ENCRYPTION_KEY`のテスト用デフォルト値を追加。
- `admin-ui/tenant-portal.html` — 「SIM管理」タブ追加、デバイスタブにSIMバッジ追加。
- `platform-ui/tenant.html` — SORACOM連携バッジ追加。
- `install-aws.sh` / `install-mac.sh` / `install-ubuntu.sh` / `.env.example` / `docker-compose.yml` — `SECRETS_ENCRYPTION_KEY`の生成・配線。
- `docs/design.html` — 機能概要の追記。

---

### Task 1: 暗号化ヘルパー（`services/crypto.py`）と環境変数

**Files:**
- Create: `core-api/app/services/crypto.py`
- Modify: `core-api/app/config.py`
- Modify: `core-api/tests/conftest.py`
- Test: `core-api/tests/test_crypto.py`

**Interfaces:**
- Consumes: `app.config.settings.secrets_encryption_key`（新規、`str`、`Field(min_length=32)`）。
- Produces: `encrypt_secret(plaintext: str) -> str`、`decrypt_secret(ciphertext: str) -> str`。以降の全タスクがこれを使う。

- [ ] **Step 1: 失敗するテストを書く**

```python
# core-api/tests/test_crypto.py
import pytest

from app.services.crypto import decrypt_secret, encrypt_secret


def test_encrypt_then_decrypt_roundtrip():
    ciphertext = encrypt_secret("super-secret-value")
    assert ciphertext != "super-secret-value"
    assert decrypt_secret(ciphertext) == "super-secret-value"


def test_ciphertext_does_not_contain_the_plaintext():
    ciphertext = encrypt_secret("keyId-abcdefg12345")
    assert "keyId-abcdefg12345" not in ciphertext


def test_decrypt_with_wrong_key_fails(monkeypatch):
    from app.services import crypto
    ciphertext = encrypt_secret("value-a")
    monkeypatch.setattr(crypto.settings, "secrets_encryption_key", "a-completely-different-32-char-key!")
    with pytest.raises(Exception):
        decrypt_secret(ciphertext)


def test_decrypt_garbage_raises():
    with pytest.raises(Exception):
        decrypt_secret("not-a-valid-fernet-token")
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_crypto.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.crypto'`

- [ ] **Step 3: `config.py`に環境変数を追加する**

`core-api/app/config.py`の`platform_domain: str = "localhost"`の次の行に追加:

```python
    # Fernet暗号化の鍵として使う秘密文字列(SORACOM認証情報等の暗号化に使う)。32文字以上。
    # 任意の文字列でよい(内部でSHA-256に通してFernet鍵へ変換するため、base64形式である必要はない)。
    secrets_encryption_key: str = Field(min_length=32)
```

- [ ] **Step 4: `crypto.py`を実装する**

```python
# core-api/app/services/crypto.py
"""汎用の対称鍵暗号ヘルパー。SORACOM認証情報など、復号して使う必要がある秘密情報の保存に使う
(パスワードのような一方向ハッシュでは、送信時にAPIへ渡す平文を復元できないため使えない)。"""
import base64
import hashlib

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    # secrets_encryption_keyは任意長・任意文字列でよい運用にするため、SHA-256で32byteに固定してから
    # urlsafe base64化する(Fernetの鍵はこの形式でなければならない)。インストーラはopenssl rand -hex 32
    # のような既存の秘密生成と同じ方法でこの値を生成でき、Fernet鍵の生成手順を別途覚える必要がない。
    digest = hashlib.sha256(settings.secrets_encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
```

- [ ] **Step 5: `conftest.py`にテスト用の既定値を追加する**

`core-api/tests/conftest.py`の`os.environ.setdefault("EMQX_WEBHOOK_SECRET", ...)`の次の行に追加:

```python
os.environ.setdefault("SECRETS_ENCRYPTION_KEY", "test_secrets_encryption_key_at_least_32_chars")
```

- [ ] **Step 6: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_crypto.py -v`
Expected: 4 passed

- [ ] **Step 7: コミット**

```bash
git add core-api/app/services/crypto.py core-api/app/config.py core-api/tests/conftest.py core-api/tests/test_crypto.py
git commit -m "feat(sim): SORACOM認証情報暗号化用のFernetヘルパーを追加"
```

---

### Task 2: SORACOM認証情報の保存（`tenant_soracom_credentials`）

**Files:**
- Modify: `core-api/app/database.py`
- Modify: `core-api/app/main.py`
- Create: `core-api/app/services/soracom_credentials.py`
- Test: `core-api/tests/test_soracom_credentials.py`

**Interfaces:**
- Consumes: Task 1の`encrypt_secret`/`decrypt_secret`。
- Produces:
  - `get_credentials(tenant_id: str) -> dict | None` — `{"coverage": str, "auth_key_id": str, "auth_key": str}`（復号済み）、未設定なら`None`。
  - `get_credentials_summary(tenant_id: str) -> dict` — `{"configured": bool, "coverage": str | None, "auth_key_id_hint": str | None}`（Auth Key本体は含まない）。
  - `is_configured(tenant_id: str) -> bool`
  - `save_credentials(tenant_id: str, coverage: str, auth_key_id: str | None, auth_key: str | None) -> None` — `auth_key_id`/`auth_key`が両方`None`なら既存の認証情報を保持したまま`coverage`だけ更新。片方だけが指定された場合は`ValueError`。
  - `delete_credentials(tenant_id: str) -> None`

- [ ] **Step 1: 失敗するテストを書く**

```python
# core-api/tests/test_soracom_credentials.py
from unittest.mock import MagicMock, patch

import pytest

from app.services.soracom_credentials import (
    delete_credentials,
    get_credentials,
    get_credentials_summary,
    is_configured,
    save_credentials,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _conn_with_row(row):
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value.fetchone.return_value = row
    return conn


def test_get_credentials_returns_none_when_not_configured():
    conn = _conn_with_row(None)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        assert get_credentials(TENANT_ID) is None
        assert is_configured(TENANT_ID) is False


def test_get_credentials_decrypts_the_stored_key():
    from app.services.crypto import encrypt_secret
    row = MagicMock(coverage="jp", auth_key_id="keyId-abc", auth_key_enc=encrypt_secret("secret-xyz"))
    conn = _conn_with_row(row)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        creds = get_credentials(TENANT_ID)
    assert creds == {"coverage": "jp", "auth_key_id": "keyId-abc", "auth_key": "secret-xyz"}


def test_summary_never_includes_the_key_and_shows_a_hint():
    row = MagicMock(coverage="g", auth_key_id="keyId-abcdefgh1234")
    conn = _conn_with_row(row)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        summary = get_credentials_summary(TENANT_ID)
    assert summary == {"configured": True, "coverage": "g", "auth_key_id_hint": "...1234"}


def test_summary_when_not_configured():
    conn = _conn_with_row(None)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        assert get_credentials_summary(TENANT_ID) == {
            "configured": False, "coverage": None, "auth_key_id_hint": None,
        }


def test_save_credentials_encrypts_before_storing():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        save_credentials(TENANT_ID, "jp", "keyId-new", "secret-new")
    sql, params = conn.execute.call_args.args
    assert "tenant_soracom_credentials" in str(sql)
    assert params["auth_key_id"] == "keyId-new"
    assert params["auth_key_enc"] != "secret-new"


def test_save_credentials_keeps_existing_key_when_both_omitted():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        save_credentials(TENANT_ID, "g", None, None)
    sql = str(conn.execute.call_args.args[0])
    assert "auth_key_enc" not in sql  # auth_key列を更新するUPDATE文には含まれない


def test_save_credentials_rejects_only_one_of_the_pair():
    with pytest.raises(ValueError):
        save_credentials(TENANT_ID, "jp", "keyId-only", None)
    with pytest.raises(ValueError):
        save_credentials(TENANT_ID, "jp", None, "secret-only")


def test_delete_credentials():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        delete_credentials(TENANT_ID)
    sql = str(conn.execute.call_args.args[0])
    assert "DELETE FROM tenant_soracom_credentials" in sql
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_soracom_credentials.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.soracom_credentials'`

- [ ] **Step 3: `database.py`にマイグレーションを追加する**

`core-api/app/database.py`の`migrate_create_revoked_tokens()`の直後に追加（既存のテーブル作成マイグレーションと同じ場所・同じ書式）:

```python
def migrate_create_tenant_soracom_credentials() -> None:
    """テナント単位のSORACOM認証情報を保持するテーブルを作成する（べき等）。
    Auth Key本体(auth_key_enc)はapp.services.cryptoで暗号化した値を保存する。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tenant_soracom_credentials (
                tenant_id    UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
                coverage     VARCHAR(10) NOT NULL DEFAULT 'jp' CHECK (coverage IN ('jp', 'g')),
                auth_key_id  VARCHAR(255) NOT NULL,
                auth_key_enc TEXT NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.commit()
```

- [ ] **Step 4: `main.py`にマイグレーションを登録する**

`core-api/app/main.py`の`from app.database import migrate_add_grafana_org_id, ... migrate_add_alert_notify_status`のimport行末尾に`, migrate_create_tenant_soracom_credentials`を追加し、`on_startup`内の`for migrate in (...)`のタプル末尾にも同じ名前を追加する（既存の2026-09-24の`migrate_add_alert_notify_status`追加時と同じ要領で、import文とタプルの両方に追記する）。

- [ ] **Step 5: `soracom_credentials.py`を実装する**

```python
# core-api/app/services/soracom_credentials.py
"""テナット単位のSORACOM認証情報(Auth Key)の保存・取得。保存時に暗号化し、APIレスポンスには
Auth Key本体を絶対に含めない(設定済みかどうかとauth_key_idの末尾だけを返す)。"""
from sqlalchemy import text

from app.database import engine
from app.services.crypto import decrypt_secret, encrypt_secret


def get_credentials(tenant_id: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT coverage, auth_key_id, auth_key_enc FROM tenant_soracom_credentials WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).fetchone()
    if not row:
        return None
    return {
        "coverage": row.coverage,
        "auth_key_id": row.auth_key_id,
        "auth_key": decrypt_secret(row.auth_key_enc),
    }


def is_configured(tenant_id: str) -> bool:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM tenant_soracom_credentials WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).fetchone()
    return row is not None


def get_credentials_summary(tenant_id: str) -> dict:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT coverage, auth_key_id FROM tenant_soracom_credentials WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        ).fetchone()
    if not row:
        return {"configured": False, "coverage": None, "auth_key_id_hint": None}
    return {"configured": True, "coverage": row.coverage, "auth_key_id_hint": "..." + row.auth_key_id[-4:]}


def save_credentials(tenant_id: str, coverage: str, auth_key_id: str | None, auth_key: str | None) -> None:
    if (auth_key_id is None) != (auth_key is None):
        raise ValueError("auth_key_id and auth_key must be provided together")

    with engine.begin() as conn:
        if auth_key_id is None:
            # coverageだけの更新。既存の認証情報が無ければ対象行が無いのでno-opになる
            conn.execute(
                text("UPDATE tenant_soracom_credentials SET coverage = :coverage, updated_at = NOW() WHERE tenant_id = :tid"),
                {"tid": tenant_id, "coverage": coverage},
            )
        else:
            conn.execute(
                text("""
                    INSERT INTO tenant_soracom_credentials (tenant_id, coverage, auth_key_id, auth_key_enc)
                    VALUES (:tid, :coverage, :auth_key_id, :auth_key_enc)
                    ON CONFLICT (tenant_id) DO UPDATE SET
                        coverage = EXCLUDED.coverage,
                        auth_key_id = EXCLUDED.auth_key_id,
                        auth_key_enc = EXCLUDED.auth_key_enc,
                        updated_at = NOW()
                """),
                {
                    "tid": tenant_id, "coverage": coverage,
                    "auth_key_id": auth_key_id, "auth_key_enc": encrypt_secret(auth_key),
                },
            )


def delete_credentials(tenant_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM tenant_soracom_credentials WHERE tenant_id = :tid"), {"tid": tenant_id})
```

- [ ] **Step 6: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_soracom_credentials.py -v`
Expected: 8 passed

- [ ] **Step 7: 実DBでマイグレーションを確認する（Dockerが動いている場合）**

Run: `docker compose exec core-api python -c "from app.database import migrate_create_tenant_soracom_credentials; migrate_create_tenant_soracom_credentials(); print('ok')"`
Expected: `ok`。続けて `docker compose exec postgres psql -U <user> -d <db> -c "\d tenant_soracom_credentials"` でテーブル定義を確認する。

- [ ] **Step 8: コミット**

```bash
git add core-api/app/database.py core-api/app/main.py core-api/app/services/soracom_credentials.py core-api/tests/test_soracom_credentials.py
git commit -m "feat(sim): テナント単位のSORACOM認証情報を暗号化して保存するtenant_soracom_credentialsを追加"
```

---

### Task 3: SORACOM APIクライアント（認証・一覧・詳細取得）

**Files:**
- Create: `core-api/app/services/soracom_client.py`
- Test: `core-api/tests/test_soracom_client.py`

**Interfaces:**
- Consumes: なし（`httpx`のみ、credentialsは呼び出し元から渡される）。
- Produces:
  - `class SoracomAuthError(Exception)` — 認証失敗（認証情報が無効）。
  - `class SoracomApiError(Exception)` — その他のAPIエラー。`str(e)`が利用者に見せてよい日本語メッセージ。
  - `list_subscribers(tenant_id, coverage, auth_key_id, auth_key, *, status_filter=None, speed_class_filter=None, tag_name=None, tag_value=None, limit=20, last_evaluated_key=None) -> tuple[list[dict], str | None]` — `(items, next_cursor)`。
  - `get_subscriber(tenant_id, coverage, auth_key_id, auth_key, imsi) -> dict | None` — 404は`None`。
  - `verify_credentials(tenant_id, coverage, auth_key_id, auth_key) -> bool`
  - `forget_token(tenant_id)` — テスト・認証情報変更後のキャッシュクリア用。

- [ ] **Step 1: 失敗するテストを書く**

```python
# core-api/tests/test_soracom_client.py
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services import soracom_client
from app.services.soracom_client import (
    SoracomApiError,
    SoracomAuthError,
    forget_token,
    get_subscriber,
    list_subscribers,
    verify_credentials,
)

TENANT_ID = "22222222-2222-2222-2222-222222222222"
ARGS = (TENANT_ID, "jp", "keyId-test", "test-auth-key-secret")


@pytest.fixture(autouse=True)
def _clear_cache():
    forget_token(TENANT_ID)
    yield
    forget_token(TENANT_ID)


def _auth_response():
    r = MagicMock(status_code=200)
    r.json.return_value = {"apiKey": "api-xxx", "token": "token-xxx", "operatorId": "OP123"}
    return r


def test_list_subscribers_authenticates_then_lists_with_correct_headers_and_base_url():
    auth_resp = _auth_response()
    list_resp = MagicMock(status_code=200, headers={"x-soracom-next-key": "next-imsi-key"})
    list_resp.json.return_value = [{"imsi": "4401", "status": "active"}]
    with patch("app.services.soracom_client.httpx.post", return_value=auth_resp) as mock_post, \
         patch("app.services.soracom_client.httpx.get", return_value=list_resp) as mock_get:
        items, next_cursor = list_subscribers(*ARGS, limit=10)

    assert mock_post.call_args.args[0] == "https://api.soracom.io/v1/auth"
    assert mock_post.call_args.kwargs["json"] == {"authKeyId": "keyId-test", "authKey": "test-auth-key-secret"}
    get_args, get_kwargs = mock_get.call_args
    assert get_args[0] == "https://api.soracom.io/v1/subscribers"
    assert get_kwargs["headers"] == {"X-Soracom-API-Key": "api-xxx", "X-Soracom-Token": "token-xxx"}
    assert get_kwargs["params"]["limit"] == 10
    assert items == [{"imsi": "4401", "status": "active"}]
    assert next_cursor == "next-imsi-key"


def test_list_subscribers_uses_global_coverage_base_url():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()), \
         patch("app.services.soracom_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, headers={}, json=lambda: [])
        list_subscribers(TENANT_ID, "g", "keyId-test", "secret", limit=10)
    assert mock_get.call_args.args[0] == "https://g.api.soracom.io/v1/subscribers"


def test_list_subscribers_passes_filters_through():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()), \
         patch("app.services.soracom_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, headers={}, json=lambda: [])
        list_subscribers(*ARGS, status_filter="active|inactive", speed_class_filter="s1.standard",
                         tag_name="env", tag_value="prod", last_evaluated_key="prev-key")
    params = mock_get.call_args.kwargs["params"]
    assert params["status_filter"] == "active|inactive"
    assert params["speed_class_filter"] == "s1.standard"
    assert params["tag_name"] == "env" and params["tag_value"] == "prod"
    assert params["last_evaluated_key"] == "prev-key"


def test_auth_failure_raises_soracom_auth_error():
    with patch("app.services.soracom_client.httpx.post", return_value=MagicMock(status_code=401)):
        with pytest.raises(SoracomAuthError):
            list_subscribers(*ARGS)


def test_token_is_cached_across_calls():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()) as mock_post, \
         patch("app.services.soracom_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, headers={}, json=lambda: [])
        list_subscribers(*ARGS)
        list_subscribers(*ARGS)
    assert mock_post.call_count == 1  # 2回目はキャッシュされたトークンを使う


def test_expired_token_triggers_one_reauth_and_retry():
    unauthorized = MagicMock(status_code=401, text="")
    ok = MagicMock(status_code=200, headers={}, json=lambda: [])
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()) as mock_post, \
         patch("app.services.soracom_client.httpx.get", side_effect=[unauthorized, ok]) as mock_get:
        items, _ = list_subscribers(*ARGS)
    assert mock_post.call_count == 2  # 最初の認証 + 401を受けての再認証
    assert mock_get.call_count == 2
    assert items == []


def test_get_subscriber_returns_none_on_404():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()), \
         patch("app.services.soracom_client.httpx.get", return_value=MagicMock(status_code=404, text="")):
        assert get_subscriber(*ARGS, "4401") is None


def test_network_timeout_raises_soracom_api_error_without_leaking_credentials():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()), \
         patch("app.services.soracom_client.httpx.get", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(SoracomApiError) as exc_info:
            list_subscribers(*ARGS)
    assert "test-auth-key-secret" not in str(exc_info.value)


def test_verify_credentials_true_and_false():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()):
        assert verify_credentials(*ARGS) is True
    forget_token(TENANT_ID)
    with patch("app.services.soracom_client.httpx.post", return_value=MagicMock(status_code=401)):
        assert verify_credentials(*ARGS) is False
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_soracom_client.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.soracom_client'`

- [ ] **Step 3: `soracom_client.py`を実装する**

```python
# core-api/app/services/soracom_client.py
"""SORACOM APIの呼び出し本体。認証トークンはテナントごとにプロセス内メモリでキャッシュし、
期限切れ(401)を受けたら1回だけ再認証してリトライする。エラーは利用者に見せてよい日本語メッセージに
変換し、認証情報・トークンはメッセージにも例外にも含めない。"""
import threading
import time

import httpx

_BASE_URLS = {
    "jp": "https://api.soracom.io/v1",
    "g": "https://g.api.soracom.io/v1",
}
# SORACOMのトークンは既定24時間(86400秒)有効。それより手前でキャッシュを切り、
# 実際の期限切れより先にこちらから再認証しておく。
_TOKEN_TTL_SEC = 23 * 3600
_TIMEOUT = 10.0

_lock = threading.Lock()
# tenant_id -> (api_key, token, expires_at_monotonic)
_token_cache: dict[str, tuple[str, str, float]] = {}


class SoracomAuthError(Exception):
    """認証情報(Auth Key)が無効、または認証に失敗した。"""


class SoracomApiError(Exception):
    """認証以外のAPI呼び出しの失敗。str(e)はそのまま利用者に見せてよい。"""


def forget_token(tenant_id: str) -> None:
    with _lock:
        _token_cache.pop(tenant_id, None)


def _base_url(coverage: str) -> str:
    return _BASE_URLS.get(coverage, _BASE_URLS["jp"])


def _authenticate(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str) -> tuple[str, str]:
    try:
        resp = httpx.post(
            f"{_base_url(coverage)}/auth",
            json={"authKeyId": auth_key_id, "authKey": auth_key},
            timeout=_TIMEOUT,
        )
    except Exception as e:
        raise SoracomAuthError(f"SORACOMへの接続に失敗しました（{type(e).__name__}）") from e
    if resp.status_code == 401:
        raise SoracomAuthError("SORACOMの認証情報が正しくありません")
    if resp.status_code != 200:
        raise SoracomAuthError(f"SORACOM認証に失敗しました（HTTP {resp.status_code}）")
    body = resp.json()
    api_key, token = body["apiKey"], body["token"]
    with _lock:
        _token_cache[tenant_id] = (api_key, token, time.monotonic() + _TOKEN_TTL_SEC)
    return api_key, token


def _get_token(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str) -> tuple[str, str]:
    with _lock:
        cached = _token_cache.get(tenant_id)
        if cached and cached[2] > time.monotonic():
            return cached[0], cached[1]
    return _authenticate(tenant_id, coverage, auth_key_id, auth_key)


def _describe_exception(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return f"SORACOMへの接続がタイムアウトしました（{_TIMEOUT:.0f}秒）"
    if isinstance(e, httpx.NetworkError):
        return f"SORACOMに接続できません（{type(e).__name__}）"
    return f"SORACOM APIの呼び出しに失敗しました（{type(e).__name__}）"


def _describe_http_failure(status_code: int) -> str:
    if status_code == 429:
        return "SORACOM APIの呼び出し回数制限に達しました。しばらくしてから再度お試しください"
    if status_code >= 500:
        return "SORACOM側で一時的な問題が発生しています"
    return f"SORACOM APIがエラーを返しました（HTTP {status_code}）"


def _request(method: str, tenant_id: str, coverage: str, auth_key_id: str, auth_key: str,
            path: str, **kwargs) -> httpx.Response:
    """認証済みでSORACOM APIを呼ぶ。401を受けたら1回だけ再認証してリトライする。
    404はそのまま呼び出し元に返す(呼び出し元が「対象が無い」として扱う)。"""
    api_key, token = _get_token(tenant_id, coverage, auth_key_id, auth_key)
    url = f"{_base_url(coverage)}{path}"
    headers = {"X-Soracom-API-Key": api_key, "X-Soracom-Token": token}
    call = httpx.get if method == "GET" else httpx.post

    try:
        resp = call(url, headers=headers, timeout=_TIMEOUT, **kwargs)
    except Exception as e:
        raise SoracomApiError(_describe_exception(e)) from e

    if resp.status_code == 401:
        forget_token(tenant_id)
        api_key, token = _authenticate(tenant_id, coverage, auth_key_id, auth_key)
        headers = {"X-Soracom-API-Key": api_key, "X-Soracom-Token": token}
        try:
            resp = call(url, headers=headers, timeout=_TIMEOUT, **kwargs)
        except Exception as e:
            raise SoracomApiError(_describe_exception(e)) from e

    if resp.status_code == 404:
        return resp
    if resp.status_code >= 400:
        raise SoracomApiError(_describe_http_failure(resp.status_code))
    return resp


def list_subscribers(
    tenant_id: str, coverage: str, auth_key_id: str, auth_key: str, *,
    status_filter: str | None = None, speed_class_filter: str | None = None,
    tag_name: str | None = None, tag_value: str | None = None,
    limit: int = 20, last_evaluated_key: str | None = None,
) -> tuple[list[dict], str | None]:
    params: dict = {"limit": limit}
    if status_filter:
        params["status_filter"] = status_filter
    if speed_class_filter:
        params["speed_class_filter"] = speed_class_filter
    if tag_name:
        params["tag_name"] = tag_name
    if tag_value:
        params["tag_value"] = tag_value
    if last_evaluated_key:
        params["last_evaluated_key"] = last_evaluated_key

    resp = _request("GET", tenant_id, coverage, auth_key_id, auth_key, "/subscribers", params=params)
    return resp.json(), resp.headers.get("x-soracom-next-key")


def get_subscriber(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str, imsi: str) -> dict | None:
    resp = _request("GET", tenant_id, coverage, auth_key_id, auth_key, f"/subscribers/{imsi}")
    return None if resp.status_code == 404 else resp.json()


def verify_credentials(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str) -> bool:
    try:
        _authenticate(tenant_id, coverage, auth_key_id, auth_key)
        return True
    except SoracomAuthError:
        return False
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_soracom_client.py -v`
Expected: 10 passed

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/soracom_client.py core-api/tests/test_soracom_client.py
git commit -m "feat(sim): SORACOM APIクライアント(認証キャッシュ・一覧/詳細取得)を追加"
```

---

### Task 4: SORACOM APIクライアント（活性化・停止・速度クラス変更）

**Files:**
- Modify: `core-api/app/services/soracom_client.py`
- Test: `core-api/tests/test_soracom_client.py`（追記）

**Interfaces:**
- Consumes: Task 3の`_request`。
- Produces: `activate_subscriber(tenant_id, coverage, auth_key_id, auth_key, imsi) -> dict | None`、`deactivate_subscriber(...) -> dict | None`、`update_speed_class(tenant_id, coverage, auth_key_id, auth_key, imsi, speed_class) -> dict | None`（いずれも404は`None`）。

- [ ] **Step 1: 失敗するテストを追記する**

`core-api/tests/test_soracom_client.py`の末尾に追記:

```python
from app.services.soracom_client import activate_subscriber, deactivate_subscriber, update_speed_class


def test_activate_subscriber_posts_to_the_activate_path():
    updated = MagicMock(status_code=200)
    updated.json.return_value = {"imsi": "4401", "status": "active"}
    with patch("app.services.soracom_client.httpx.post", side_effect=[_auth_response(), updated]) as mock_post:
        result = activate_subscriber(*ARGS, "4401")
    assert mock_post.call_args.args[0] == "https://api.soracom.io/v1/subscribers/4401/activate"
    assert result == {"imsi": "4401", "status": "active"}


def test_deactivate_subscriber_returns_none_on_404():
    not_found = MagicMock(status_code=404, text="")
    with patch("app.services.soracom_client.httpx.post", side_effect=[_auth_response(), not_found]):
        assert deactivate_subscriber(*ARGS, "0000") is None


def test_update_speed_class_sends_the_speed_class_body():
    updated = MagicMock(status_code=200)
    updated.json.return_value = {"imsi": "4401", "speedClass": "s1.fast"}
    with patch("app.services.soracom_client.httpx.post", side_effect=[_auth_response(), updated]) as mock_post:
        update_speed_class(*ARGS, "4401", "s1.fast")
    call_args, call_kwargs = mock_post.call_args
    assert call_args[0] == "https://api.soracom.io/v1/subscribers/4401/update_speed_class"
    assert call_kwargs["json"] == {"speedClass": "s1.fast"}
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_soracom_client.py -v -k "activate or deactivate or speed_class"`
Expected: `ImportError: cannot import name 'activate_subscriber'`

- [ ] **Step 3: `soracom_client.py`に3関数を追加する**

`core-api/app/services/soracom_client.py`の`get_subscriber`の直後に追加:

```python
def activate_subscriber(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str, imsi: str) -> dict | None:
    resp = _request("POST", tenant_id, coverage, auth_key_id, auth_key, f"/subscribers/{imsi}/activate")
    return None if resp.status_code == 404 else resp.json()


def deactivate_subscriber(tenant_id: str, coverage: str, auth_key_id: str, auth_key: str, imsi: str) -> dict | None:
    resp = _request("POST", tenant_id, coverage, auth_key_id, auth_key, f"/subscribers/{imsi}/deactivate")
    return None if resp.status_code == 404 else resp.json()


def update_speed_class(
    tenant_id: str, coverage: str, auth_key_id: str, auth_key: str, imsi: str, speed_class: str,
) -> dict | None:
    resp = _request(
        "POST", tenant_id, coverage, auth_key_id, auth_key, f"/subscribers/{imsi}/update_speed_class",
        json={"speedClass": speed_class},
    )
    return None if resp.status_code == 404 else resp.json()
```

`_request`の`call = httpx.get if method == "GET" else httpx.post`はすでに`json=`等の`**kwargs`をそのまま`httpx.post`へ渡すため、`update_speed_class`の`json=`はそのまま機能する。

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_soracom_client.py -v`
Expected: 13 passed

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/soracom_client.py core-api/tests/test_soracom_client.py
git commit -m "feat(sim): SORACOM回線のactivate/deactivate/速度クラス変更を追加"
```

---

### Task 5: 回線とデバイスの紐づけ（`sim_bindings`テーブルとbind/unbind）

**Files:**
- Modify: `core-api/app/database.py`
- Create: `core-api/app/services/sim_service.py`
- Test: `core-api/tests/test_sim_service.py`

**Interfaces:**
- Consumes: なし（このタスクでは`sim_bindings`テーブルのCRUDのみ。SORACOM呼び出しはTask 6・7で追加）。
- Produces:
  - `ensure_sim_tables_to_tenant_schema(tenant_id: str) -> None`（`database.py`。既存の`add_firmware_tables_to_tenant_schema`と同じ、呼び出しごとに`CREATE TABLE IF NOT EXISTS`する遅延作成パターン）。
  - `class DeviceNotFoundError(Exception)`、`class SimAlreadyBoundError(Exception)`、`class DeviceAlreadyBoundError(Exception)`、`class BindingNotFoundError(Exception)`（`sim_service.py`）。
  - `bind_line(tenant_id: str, imsi: str, device_id: str, iccid: str | None = None) -> None`
  - `unbind_line(tenant_id: str, imsi: str) -> None`
  - `get_bindings_by_imsi(tenant_id: str, imsis: list[str]) -> dict[str, dict]` — `{imsi: {"device_id":..., "device_name":...}}`。

- [ ] **Step 1: 失敗するテストを書く**

```python
# core-api/tests/test_sim_service.py
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from app.services.sim_service import (
    BindingNotFoundError,
    DeviceAlreadyBoundError,
    DeviceNotFoundError,
    SimAlreadyBoundError,
    bind_line,
    get_bindings_by_imsi,
    unbind_line,
)

TENANT_ID = "33333333-3333-3333-3333-333333333333"
SCHEMA = "tenant_33333333_3333_3333_3333_333333333333"


def _conn():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def test_bind_line_rejects_a_missing_device():
    conn = _conn()
    conn.execute.return_value.fetchone.return_value = None  # devicesに無い
    with patch("app.services.sim_service.ensure_sim_tables_to_tenant_schema"), \
         patch("app.services.sim_service.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        with pytest.raises(DeviceNotFoundError):
            bind_line(TENANT_ID, "440100000001", "dev-001")


def test_bind_line_inserts_when_device_exists():
    conn = _conn()
    conn.execute.return_value.fetchone.return_value = (1,)  # devicesに存在
    with patch("app.services.sim_service.ensure_sim_tables_to_tenant_schema"), \
         patch("app.services.sim_service.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        bind_line(TENANT_ID, "440100000001", "dev-001", iccid="8981100005...")
    insert_sql = str(conn.execute.call_args_list[-1].args[0])
    insert_params = conn.execute.call_args_list[-1].args[1]
    assert "INSERT INTO" in insert_sql and "sim_bindings" in insert_sql
    assert insert_params["imsi"] == "440100000001" and insert_params["device_id"] == "dev-001"


@pytest.mark.parametrize("pg_error_detail,expected", [
    ("Key (imsi)=(440100000001) already exists.", SimAlreadyBoundError),
    ("Key (device_id)=(dev-001) already exists.", DeviceAlreadyBoundError),
])
def test_bind_line_translates_unique_violations(pg_error_detail, expected):
    conn = _conn()
    conn.execute.return_value.fetchone.return_value = (1,)
    orig = MagicMock()
    orig.diag.message_detail = pg_error_detail
    conn.execute.side_effect = [MagicMock(fetchone=lambda: (1,)), IntegrityError("stmt", {}, orig)]
    with patch("app.services.sim_service.ensure_sim_tables_to_tenant_schema"), \
         patch("app.services.sim_service.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        with pytest.raises(expected):
            bind_line(TENANT_ID, "440100000001", "dev-001")


def test_unbind_line_raises_when_no_binding_exists():
    conn = _conn()
    conn.execute.return_value.rowcount = 0
    with patch("app.services.sim_service.ensure_sim_tables_to_tenant_schema"), \
         patch("app.services.sim_service.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        with pytest.raises(BindingNotFoundError):
            unbind_line(TENANT_ID, "440100000001")


def test_unbind_line_deletes_the_row():
    conn = _conn()
    conn.execute.return_value.rowcount = 1
    with patch("app.services.sim_service.ensure_sim_tables_to_tenant_schema"), \
         patch("app.services.sim_service.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        unbind_line(TENANT_ID, "440100000001")
    sql = str(conn.execute.call_args.args[0])
    assert "DELETE FROM" in sql and "sim_bindings" in sql


def test_get_bindings_by_imsi_returns_a_dict_keyed_by_imsi():
    row = MagicMock(imsi="440100000001", device_id="dev-001", device_name="センサー01")
    conn = _conn()
    conn.execute.return_value.fetchall.return_value = [row]
    with patch("app.services.sim_service.ensure_sim_tables_to_tenant_schema"), \
         patch("app.services.sim_service.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        result = get_bindings_by_imsi(TENANT_ID, ["440100000001", "440100000002"])
    assert result == {"440100000001": {"device_id": "dev-001", "device_name": "センサー01"}}


def test_get_bindings_by_imsi_with_empty_list_skips_the_query():
    with patch("app.services.sim_service.ensure_sim_tables_to_tenant_schema"), \
         patch("app.services.sim_service.engine") as mock_engine:
        assert get_bindings_by_imsi(TENANT_ID, []) == {}
    mock_engine.connect.assert_not_called()
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_sim_service.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.sim_service'`

- [ ] **Step 3: `database.py`に`ensure_sim_tables_to_tenant_schema`を追加する**

`core-api/app/database.py`の`add_firmware_tables_to_tenant_schema`の直後に追加（同じ遅延作成パターン）:

```python
def ensure_sim_tables_to_tenant_schema(tenant_id: str) -> None:
    """SIMとデバイスの紐づけを保持するsim_bindingsテーブルを作成する（べき等）。
    firmware_releasesと同様、呼び出しごとにCREATE TABLE IF NOT EXISTSする遅延作成。"""
    import re
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', tenant_id.lower()):
        raise ValueError(f"Invalid tenant_id: {tenant_id}")
    schema = f"tenant_{tenant_id.replace('-', '_')}"
    with engine.connect() as conn:
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".sim_bindings (
                id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                imsi      VARCHAR(32) NOT NULL UNIQUE,
                iccid     VARCHAR(32),
                device_id VARCHAR(255) NOT NULL UNIQUE,
                bound_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        '''))
        conn.commit()
```

- [ ] **Step 4: `sim_service.py`を実装する**

```python
# core-api/app/services/sim_service.py
"""ルーターとSORACOM呼び出しの中間層。SIM(IMSI)とデバイスの紐づけ(sim_bindings、こちら独自の情報)の
CRUDと、将来「都度SORACOM API」から「定期同期+DBキャッシュ」に切り替える際の差し替え点になる。"""
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.database import engine, ensure_sim_tables_to_tenant_schema


class DeviceNotFoundError(Exception):
    pass


class SimAlreadyBoundError(Exception):
    pass


class DeviceAlreadyBoundError(Exception):
    pass


class BindingNotFoundError(Exception):
    pass


def _schema(tenant_id: str) -> str:
    return f"tenant_{tenant_id.replace('-', '_')}"


def bind_line(tenant_id: str, imsi: str, device_id: str, iccid: str | None = None) -> None:
    schema = _schema(tenant_id)
    ensure_sim_tables_to_tenant_schema(tenant_id)
    with engine.begin() as conn:
        device = conn.execute(
            text(f'SELECT 1 FROM "{schema}".devices WHERE device_id = :did'), {"did": device_id},
        ).fetchone()
        if not device:
            raise DeviceNotFoundError(device_id)
        try:
            conn.execute(
                text(f'''
                    INSERT INTO "{schema}".sim_bindings (imsi, iccid, device_id)
                    VALUES (:imsi, :iccid, :device_id)
                '''),
                {"imsi": imsi, "iccid": iccid, "device_id": device_id},
            )
        except IntegrityError as e:
            detail = str(getattr(getattr(e, "orig", None), "diag", None) and e.orig.diag.message_detail or e)
            if "imsi" in detail:
                raise SimAlreadyBoundError(imsi) from e
            raise DeviceAlreadyBoundError(device_id) from e


def unbind_line(tenant_id: str, imsi: str) -> None:
    schema = _schema(tenant_id)
    ensure_sim_tables_to_tenant_schema(tenant_id)
    with engine.begin() as conn:
        result = conn.execute(text(f'DELETE FROM "{schema}".sim_bindings WHERE imsi = :imsi'), {"imsi": imsi})
        if result.rowcount == 0:
            raise BindingNotFoundError(imsi)


def get_bindings_by_imsi(tenant_id: str, imsis: list[str]) -> dict[str, dict]:
    if not imsis:
        return {}
    schema = _schema(tenant_id)
    ensure_sim_tables_to_tenant_schema(tenant_id)
    with engine.connect() as conn:
        rows = conn.execute(
            text(f'''
                SELECT b.imsi, b.device_id, d.device_name
                FROM "{schema}".sim_bindings b
                LEFT JOIN "{schema}".devices d ON d.device_id = b.device_id
                WHERE b.imsi = ANY(:imsis)
            '''),
            {"imsis": imsis},
        ).fetchall()
    return {r.imsi: {"device_id": r.device_id, "device_name": r.device_name or r.device_id} for r in rows}
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_sim_service.py -v`
Expected: 7 passed

- [ ] **Step 6: 実DBでbind/unbindを確認する（Dockerが動いている場合）**

適当なテナントID・既存のdevice_idで`bind_line`→`get_bindings_by_imsi`→`unbind_line`を順に呼び、`psql`で`sim_bindings`テーブルの行が作成・削除されることを確認する。

- [ ] **Step 7: コミット**

```bash
git add core-api/app/database.py core-api/app/services/sim_service.py core-api/tests/test_sim_service.py
git commit -m "feat(sim): SIMとデバイスの紐づけ(sim_bindings)のbind/unbindを追加"
```

---

### Task 6: 回線一覧・詳細の中間層（SORACOM連携 + 紐づけ情報の付加）

**Files:**
- Modify: `core-api/app/services/sim_service.py`
- Test: `core-api/tests/test_sim_service.py`（追記）

**Interfaces:**
- Consumes: Task 2の`get_credentials`、Task 3の`list_subscribers`/`get_subscriber`、Task 5の`get_bindings_by_imsi`。
- Produces:
  - `class SoracomNotConfiguredError(Exception)`
  - `list_lines(tenant_id, *, status=None, speed_class=None, tag_name=None, tag_value=None, bound=None, cursor=None, limit=20) -> dict` — `{"items": [...], "next_cursor": str | None}`。各itemは`{"imsi","iccid","status","speed_class","bound_device_id","bound_device_name", ...}`。
  - `get_line(tenant_id, imsi) -> dict | None`

- [ ] **Step 1: 失敗するテストを追記する**

`core-api/tests/test_sim_service.py`の末尾に追記:

```python
from app.services.sim_service import SoracomNotConfiguredError, get_line, list_lines

CREDS = {"coverage": "jp", "auth_key_id": "keyId-x", "auth_key": "secret-x"}


def test_list_lines_raises_when_not_configured():
    with patch("app.services.sim_service.get_credentials", return_value=None):
        with pytest.raises(SoracomNotConfiguredError):
            list_lines(TENANT_ID)


def test_list_lines_merges_binding_info_and_passes_the_cursor_through():
    subscribers = [{"imsi": "440100000001", "iccid": "8981...01", "status": "active", "speedClass": "s1.standard"},
                   {"imsi": "440100000002", "iccid": "8981...02", "status": "inactive", "speedClass": "s1.standard"}]
    with patch("app.services.sim_service.get_credentials", return_value=CREDS), \
         patch("app.services.sim_service.list_subscribers", return_value=(subscribers, "next-key")) as mock_list, \
         patch("app.services.sim_service.get_bindings_by_imsi",
               return_value={"440100000001": {"device_id": "dev-001", "device_name": "センサー01"}}):
        result = list_lines(TENANT_ID, cursor="prev-key", limit=2)

    assert mock_list.call_args.kwargs["last_evaluated_key"] == "prev-key"
    assert mock_list.call_args.kwargs["limit"] == 2
    assert result["next_cursor"] == "next-key"
    assert result["items"][0]["bound_device_id"] == "dev-001"
    assert result["items"][0]["bound_device_name"] == "センサー01"
    assert result["items"][1]["bound_device_id"] is None


def test_list_lines_bound_filter_true_keeps_only_bound_items():
    subscribers = [{"imsi": "440100000001", "status": "active"}, {"imsi": "440100000002", "status": "active"}]
    with patch("app.services.sim_service.get_credentials", return_value=CREDS), \
         patch("app.services.sim_service.list_subscribers", return_value=(subscribers, None)), \
         patch("app.services.sim_service.get_bindings_by_imsi",
               return_value={"440100000001": {"device_id": "dev-001", "device_name": "dev-001"}}):
        result = list_lines(TENANT_ID, bound=True)
    assert [i["imsi"] for i in result["items"]] == ["440100000001"]


def test_list_lines_bound_filter_false_keeps_only_unbound_items():
    subscribers = [{"imsi": "440100000001", "status": "active"}, {"imsi": "440100000002", "status": "active"}]
    with patch("app.services.sim_service.get_credentials", return_value=CREDS), \
         patch("app.services.sim_service.list_subscribers", return_value=(subscribers, None)), \
         patch("app.services.sim_service.get_bindings_by_imsi",
               return_value={"440100000001": {"device_id": "dev-001", "device_name": "dev-001"}}):
        result = list_lines(TENANT_ID, bound=False)
    assert [i["imsi"] for i in result["items"]] == ["440100000002"]


def test_get_line_returns_none_when_subscriber_not_found():
    with patch("app.services.sim_service.get_credentials", return_value=CREDS), \
         patch("app.services.sim_service.get_subscriber", return_value=None):
        assert get_line(TENANT_ID, "000000") is None


def test_get_line_merges_binding_info():
    with patch("app.services.sim_service.get_credentials", return_value=CREDS), \
         patch("app.services.sim_service.get_subscriber", return_value={"imsi": "440100000001", "status": "active"}), \
         patch("app.services.sim_service.get_bindings_by_imsi",
               return_value={"440100000001": {"device_id": "dev-001", "device_name": "センサー01"}}):
        line = get_line(TENANT_ID, "440100000001")
    assert line["bound_device_id"] == "dev-001"
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_sim_service.py -v -k "list_lines or get_line"`
Expected: `ImportError: cannot import name 'SoracomNotConfiguredError'`

- [ ] **Step 3: `sim_service.py`に実装を追加する**

`core-api/app/services/sim_service.py`のimport群を次のように拡張し、関数を追加する:

```python
from app.services.soracom_client import get_subscriber, list_subscribers
from app.services.soracom_credentials import get_credentials
```

`BindingNotFoundError`クラスの直後に追加:

```python
class SoracomNotConfiguredError(Exception):
    pass


def _get_credentials_or_raise(tenant_id: str) -> dict:
    creds = get_credentials(tenant_id)
    if creds is None:
        raise SoracomNotConfiguredError(tenant_id)
    return creds


def _attach_binding(tenant_id: str, items: list[dict]) -> list[dict]:
    bindings = get_bindings_by_imsi(tenant_id, [i["imsi"] for i in items])
    for item in items:
        b = bindings.get(item["imsi"])
        item["bound_device_id"] = b["device_id"] if b else None
        item["bound_device_name"] = b["device_name"] if b else None
    return items


def list_lines(
    tenant_id: str, *, status: str | None = None, speed_class: str | None = None,
    tag_name: str | None = None, tag_value: str | None = None, bound: bool | None = None,
    cursor: str | None = None, limit: int = 20,
) -> dict:
    creds = _get_credentials_or_raise(tenant_id)
    items, next_cursor = list_subscribers(
        tenant_id, creds["coverage"], creds["auth_key_id"], creds["auth_key"],
        status_filter=status, speed_class_filter=speed_class,
        tag_name=tag_name, tag_value=tag_value, limit=limit, last_evaluated_key=cursor,
    )
    items = _attach_binding(tenant_id, items)
    if bound is True:
        items = [i for i in items if i["bound_device_id"] is not None]
    elif bound is False:
        items = [i for i in items if i["bound_device_id"] is None]
    return {"items": items, "next_cursor": next_cursor}


def get_line(tenant_id: str, imsi: str) -> dict | None:
    creds = _get_credentials_or_raise(tenant_id)
    item = get_subscriber(tenant_id, creds["coverage"], creds["auth_key_id"], creds["auth_key"], imsi)
    if item is None:
        return None
    return _attach_binding(tenant_id, [item])[0]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_sim_service.py -v`
Expected: 13 passed

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/sim_service.py core-api/tests/test_sim_service.py
git commit -m "feat(sim): 回線一覧・詳細に紐づけデバイス情報を付加するlist_lines/get_lineを追加"
```

---

### Task 7: 回線操作の中間層（activate/deactivate/速度クラス変更）

**Files:**
- Modify: `core-api/app/services/sim_service.py`
- Test: `core-api/tests/test_sim_service.py`（追記）

**Interfaces:**
- Consumes: Task 4の`activate_subscriber`/`deactivate_subscriber`/`update_speed_class`、Task 6の`_get_credentials_or_raise`。
- Produces: `activate_line(tenant_id, imsi) -> dict | None`、`deactivate_line(tenant_id, imsi) -> dict | None`、`set_speed_class(tenant_id, imsi, speed_class) -> dict | None`（404は`None`、未設定は`SoracomNotConfiguredError`）。

- [ ] **Step 1: 失敗するテストを追記する**

```python
from app.services.sim_service import activate_line, deactivate_line, set_speed_class


def test_activate_line_requires_configured_credentials():
    with patch("app.services.sim_service.get_credentials", return_value=None):
        with pytest.raises(SoracomNotConfiguredError):
            activate_line(TENANT_ID, "440100000001")


def test_activate_line_delegates_to_the_client_with_credentials():
    with patch("app.services.sim_service.get_credentials", return_value=CREDS), \
         patch("app.services.sim_service.activate_subscriber", return_value={"imsi": "440100000001", "status": "active"}) as mock_act:
        result = activate_line(TENANT_ID, "440100000001")
    mock_act.assert_called_once_with(TENANT_ID, "jp", "keyId-x", "secret-x", "440100000001")
    assert result["status"] == "active"


def test_deactivate_line_returns_none_on_404():
    with patch("app.services.sim_service.get_credentials", return_value=CREDS), \
         patch("app.services.sim_service.deactivate_subscriber", return_value=None):
        assert deactivate_line(TENANT_ID, "000000") is None


def test_set_speed_class_delegates_with_the_value():
    with patch("app.services.sim_service.get_credentials", return_value=CREDS), \
         patch("app.services.sim_service.update_speed_class", return_value={"speedClass": "s1.fast"}) as mock_upd:
        result = set_speed_class(TENANT_ID, "440100000001", "s1.fast")
    mock_upd.assert_called_once_with(TENANT_ID, "jp", "keyId-x", "secret-x", "440100000001", "s1.fast")
    assert result["speedClass"] == "s1.fast"
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_sim_service.py -v -k "activate_line or deactivate_line or speed_class"`
Expected: `ImportError: cannot import name 'activate_line'`

- [ ] **Step 3: `sim_service.py`に実装を追加する**

import文に追加: `from app.services.soracom_client import (activate_subscriber, deactivate_subscriber, get_subscriber, list_subscribers, update_speed_class)`

`get_line`関数の直後に追加:

```python
def activate_line(tenant_id: str, imsi: str) -> dict | None:
    creds = _get_credentials_or_raise(tenant_id)
    return activate_subscriber(tenant_id, creds["coverage"], creds["auth_key_id"], creds["auth_key"], imsi)


def deactivate_line(tenant_id: str, imsi: str) -> dict | None:
    creds = _get_credentials_or_raise(tenant_id)
    return deactivate_subscriber(tenant_id, creds["coverage"], creds["auth_key_id"], creds["auth_key"], imsi)


def set_speed_class(tenant_id: str, imsi: str, speed_class: str) -> dict | None:
    creds = _get_credentials_or_raise(tenant_id)
    return update_speed_class(tenant_id, creds["coverage"], creds["auth_key_id"], creds["auth_key"], imsi, speed_class)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_sim_service.py -v`
Expected: 17 passed

- [ ] **Step 5: コミット**

```bash
git add core-api/app/services/sim_service.py core-api/tests/test_sim_service.py
git commit -m "feat(sim): 回線のactivate/deactivate/速度クラス変更の中間層を追加"
```

---

### Task 8: 認証情報API（`/me/sim/credentials`）

**Files:**
- Create: `core-api/app/routers/tenant_sim.py`
- Modify: `core-api/app/main.py`
- Test: `core-api/tests/test_tenant_sim_api.py`

**Interfaces:**
- Consumes: Task 2の`get_credentials_summary`/`save_credentials`/`delete_credentials`、Task 3の`verify_credentials`。
- Produces: `GET /tenant-portal/me/sim/credentials`、`PUT .../credentials`（admin限定）、`DELETE .../credentials`（admin限定）。後続タスクが同じ`router`に追記していく。

- [ ] **Step 1: 失敗するテストを書く**

```python
# core-api/tests/test_tenant_sim_api.py
"""SIM管理APIのテスト。認証情報はadmin限定、回線操作はadmin/operatorで権限分けする。
認証情報(Auth Key)はレスポンス・エラーに含めない。"""
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "44444444-4444-4444-4444-444444444444"


def _cookies(role="operator"):
    t = create_access_token({"sub": "user-id", "email": "u@test.com", "type": "tenant",
                            "tenant_id": TENANT_ID, "role": role})
    return {"iot_token": t}


def test_get_credentials_summary():
    summary = {"configured": True, "coverage": "jp", "auth_key_id_hint": "...1234"}
    with patch("app.routers.tenant_sim.get_credentials_summary", return_value=summary):
        resp = client.get("/tenant-portal/me/sim/credentials", cookies=_cookies())
    assert resp.status_code == 200
    assert resp.json() == summary


def test_put_credentials_requires_admin():
    resp = client.put("/tenant-portal/me/sim/credentials", cookies=_cookies("operator"),
                      json={"coverage": "jp", "auth_key_id": "keyId-x", "auth_key": "secret-x"})
    assert resp.status_code == 403


def test_put_credentials_validates_before_saving():
    with patch("app.routers.tenant_sim.verify_credentials", return_value=False) as mock_verify, \
         patch("app.routers.tenant_sim.save_credentials") as mock_save:
        resp = client.put("/tenant-portal/me/sim/credentials", cookies=_cookies("admin"),
                          json={"coverage": "jp", "auth_key_id": "keyId-x", "auth_key": "secret-x"})
    assert resp.status_code == 422
    mock_verify.assert_called_once()
    mock_save.assert_not_called()


def test_put_credentials_saves_when_valid():
    with patch("app.routers.tenant_sim.verify_credentials", return_value=True), \
         patch("app.routers.tenant_sim.save_credentials") as mock_save, \
         patch("app.routers.tenant_sim.log_audit") as mock_audit:
        resp = client.put("/tenant-portal/me/sim/credentials", cookies=_cookies("admin"),
                          json={"coverage": "jp", "auth_key_id": "keyId-x", "auth_key": "secret-x"})
    assert resp.status_code == 204
    mock_save.assert_called_once_with(TENANT_ID, "jp", "keyId-x", "secret-x")
    assert mock_audit.call_args.args[3] == "save_soracom_credentials"


def test_put_credentials_coverage_only_update_skips_verification():
    with patch("app.routers.tenant_sim.verify_credentials") as mock_verify, \
         patch("app.routers.tenant_sim.save_credentials") as mock_save:
        resp = client.put("/tenant-portal/me/sim/credentials", cookies=_cookies("admin"), json={"coverage": "g"})
    assert resp.status_code == 204
    mock_verify.assert_not_called()
    mock_save.assert_called_once_with(TENANT_ID, "g", None, None)


def test_delete_credentials_requires_admin():
    resp = client.delete("/tenant-portal/me/sim/credentials", cookies=_cookies("operator"))
    assert resp.status_code == 403


def test_delete_credentials_as_admin():
    with patch("app.routers.tenant_sim.delete_credentials") as mock_delete, \
         patch("app.routers.tenant_sim.log_audit"):
        resp = client.delete("/tenant-portal/me/sim/credentials", cookies=_cookies("admin"))
    assert resp.status_code == 204
    mock_delete.assert_called_once_with(TENANT_ID)
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_sim_api.py -v`
Expected: `404 Not Found`（ルーターが未登録のため）

- [ ] **Step 3: `tenant_sim.py`を実装する**

```python
# core-api/app/routers/tenant_sim.py
"""テナントポータル向けSORACOM回線管理API。認証情報(Auth Key)はadmin限定、回線の確認・操作・
紐づけはadmin/operatorに許可する。"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.services.audit import log_audit
from app.services.soracom_client import verify_credentials
from app.services.soracom_credentials import delete_credentials, get_credentials_summary, save_credentials
from app.services.tenant_session import require_tenant_session

router = APIRouter(prefix="/tenant-portal/me/sim", tags=["tenant-sim"])

_require_tenant = require_tenant_session


def _require_admin(payload: dict = Depends(_require_tenant)) -> dict:
    if payload.get("role") not in ("admin",):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return payload


def _require_admin_or_operator(payload: dict = Depends(_require_tenant)) -> dict:
    if payload.get("role") not in ("admin", "operator"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator or admin role required")
    return payload


class CredentialsUpdate(BaseModel):
    coverage: Literal["jp", "g"] = "jp"
    auth_key_id: Optional[str] = None
    auth_key: Optional[str] = None


@router.get("/credentials")
def get_credentials_endpoint(payload: dict = Depends(_require_admin_or_operator)):
    return get_credentials_summary(payload["tenant_id"])


@router.put("/credentials", status_code=status.HTTP_204_NO_CONTENT)
def put_credentials(body: CredentialsUpdate, payload: dict = Depends(_require_admin)):
    if (body.auth_key_id is None) != (body.auth_key is None):
        raise HTTPException(status_code=422, detail="auth_key_id and auth_key must be provided together")

    tenant_id = payload["tenant_id"]
    if body.auth_key_id is not None:
        if not verify_credentials(tenant_id, body.coverage, body.auth_key_id, body.auth_key):
            raise HTTPException(status_code=422, detail="SORACOMの認証情報が正しくありません")

    save_credentials(tenant_id, body.coverage, body.auth_key_id, body.auth_key)
    log_audit("tenant", payload["sub"], payload["email"], "save_soracom_credentials",
              tenant_id=tenant_id, resource_type="soracom_credentials", resource_id="credentials")


@router.delete("/credentials", status_code=status.HTTP_204_NO_CONTENT)
def delete_credentials_endpoint(payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    delete_credentials(tenant_id)
    log_audit("tenant", payload["sub"], payload["email"], "delete_soracom_credentials",
              tenant_id=tenant_id, resource_type="soracom_credentials", resource_id="credentials")
```

- [ ] **Step 4: `main.py`にルーターを登録する**

`core-api/app/main.py`に次を追加:
- import行に`tenant_sim`を追加: `from app.routers import health, auth, ..., rag, tenant_sim`
- `app.include_router(rag.router)`の直後に`app.include_router(tenant_sim.router)`を追加。

- [ ] **Step 5: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_sim_api.py -v`
Expected: 7 passed

- [ ] **Step 6: コミット**

```bash
git add core-api/app/routers/tenant_sim.py core-api/app/main.py core-api/tests/test_tenant_sim_api.py
git commit -m "feat(sim): SORACOM認証情報のAPI(GET/PUT/DELETE /me/sim/credentials)を追加"
```

---

### Task 9: 回線一覧・詳細・操作API（`/me/sim/lines`）

**Files:**
- Modify: `core-api/app/routers/tenant_sim.py`
- Test: `core-api/tests/test_tenant_sim_api.py`（追記）

**Interfaces:**
- Consumes: Task 6・7の`list_lines`/`get_line`/`activate_line`/`deactivate_line`/`set_speed_class`、`sim_service.SoracomNotConfiguredError`。
- Produces: `GET /me/sim/lines`、`GET /me/sim/lines/{imsi}`、`POST .../activate`、`POST .../deactivate`、`POST .../speed-class`（いずれもadmin/operator）。

- [ ] **Step 1: 失敗するテストを追記する**

```python
from app.services.sim_service import SoracomNotConfiguredError


def test_list_lines_passes_filters_and_returns_the_result():
    result = {"items": [{"imsi": "440100000001", "bound_device_id": None}], "next_cursor": "next-key"}
    with patch("app.routers.tenant_sim.list_lines", return_value=result) as mock_list:
        resp = client.get(
            "/tenant-portal/me/sim/lines?status=active&speed_class=s1.standard&bound=false&cursor=prev&limit=5",
            cookies=_cookies(),
        )
    assert resp.status_code == 200
    assert resp.json() == result
    mock_list.assert_called_once_with(
        TENANT_ID, status="active", speed_class="s1.standard", tag_name=None, tag_value=None,
        bound=False, cursor="prev", limit=5,
    )


def test_list_lines_without_credentials_returns_400():
    with patch("app.routers.tenant_sim.list_lines", side_effect=SoracomNotConfiguredError(TENANT_ID)):
        resp = client.get("/tenant-portal/me/sim/lines", cookies=_cookies())
    assert resp.status_code == 400


def test_get_line_not_found_returns_404():
    with patch("app.routers.tenant_sim.get_line", return_value=None):
        resp = client.get("/tenant-portal/me/sim/lines/000000", cookies=_cookies())
    assert resp.status_code == 404


def test_activate_requires_operator_or_admin():
    resp = client.post("/tenant-portal/me/sim/lines/440100000001/activate", cookies=_cookies("viewer"))
    assert resp.status_code == 403


def test_activate_logs_the_audit_entry_without_the_imsi_leaking_credentials():
    with patch("app.routers.tenant_sim.activate_line", return_value={"imsi": "440100000001", "status": "active"}), \
         patch("app.routers.tenant_sim.log_audit") as mock_audit:
        resp = client.post("/tenant-portal/me/sim/lines/440100000001/activate", cookies=_cookies("operator"))
    assert resp.status_code == 200
    assert mock_audit.call_args.args[3] == "activate_sim"
    assert mock_audit.call_args.kwargs["resource_id"] == "440100000001"


def test_deactivate_not_found_returns_404():
    with patch("app.routers.tenant_sim.deactivate_line", return_value=None):
        resp = client.post("/tenant-portal/me/sim/lines/000000/deactivate", cookies=_cookies("operator"))
    assert resp.status_code == 404


def test_speed_class_update():
    with patch("app.routers.tenant_sim.set_speed_class", return_value={"speedClass": "s1.fast"}) as mock_set, \
         patch("app.routers.tenant_sim.log_audit"):
        resp = client.post("/tenant-portal/me/sim/lines/440100000001/speed-class",
                           cookies=_cookies("operator"), json={"speed_class": "s1.fast"})
    assert resp.status_code == 200
    mock_set.assert_called_once_with(TENANT_ID, "440100000001", "s1.fast")
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_sim_api.py -v -k "lines or activate or deactivate or speed_class"`
Expected: `404 Not Found`

- [ ] **Step 3: `tenant_sim.py`に実装を追加する**

import文を次のように拡張:

```python
from pydantic import BaseModel, Field

from app.services.sim_service import (
    SoracomNotConfiguredError,
    activate_line,
    deactivate_line,
    get_line,
    list_lines,
    set_speed_class,
)
```

ファイル末尾に追加:

```python
def _not_configured_as_400():
    raise HTTPException(status_code=400, detail="SORACOM連携が設定されていません")


@router.get("/lines")
def list_lines_endpoint(
    status: Optional[str] = None, speed_class: Optional[str] = None,
    tag_name: Optional[str] = None, tag_value: Optional[str] = None,
    bound: Optional[bool] = None, cursor: Optional[str] = None, limit: int = 20,
    payload: dict = Depends(_require_admin_or_operator),
):
    try:
        return list_lines(
            payload["tenant_id"], status=status, speed_class=speed_class,
            tag_name=tag_name, tag_value=tag_value, bound=bound, cursor=cursor, limit=limit,
        )
    except SoracomNotConfiguredError:
        _not_configured_as_400()


@router.get("/lines/{imsi}")
def get_line_endpoint(imsi: str, payload: dict = Depends(_require_admin_or_operator)):
    try:
        line = get_line(payload["tenant_id"], imsi)
    except SoracomNotConfiguredError:
        _not_configured_as_400()
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Line not found")
    return line


@router.post("/lines/{imsi}/activate")
def activate_line_endpoint(imsi: str, payload: dict = Depends(_require_admin_or_operator)):
    try:
        line = activate_line(payload["tenant_id"], imsi)
    except SoracomNotConfiguredError:
        _not_configured_as_400()
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Line not found")
    log_audit("tenant", payload["sub"], payload["email"], "activate_sim",
              tenant_id=payload["tenant_id"], resource_type="sim", resource_id=imsi)
    return line


@router.post("/lines/{imsi}/deactivate")
def deactivate_line_endpoint(imsi: str, payload: dict = Depends(_require_admin_or_operator)):
    try:
        line = deactivate_line(payload["tenant_id"], imsi)
    except SoracomNotConfiguredError:
        _not_configured_as_400()
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Line not found")
    log_audit("tenant", payload["sub"], payload["email"], "deactivate_sim",
              tenant_id=payload["tenant_id"], resource_type="sim", resource_id=imsi)
    return line


class SpeedClassUpdate(BaseModel):
    speed_class: str = Field(min_length=1, max_length=50)


@router.post("/lines/{imsi}/speed-class")
def set_speed_class_endpoint(imsi: str, body: SpeedClassUpdate, payload: dict = Depends(_require_admin_or_operator)):
    try:
        line = set_speed_class(payload["tenant_id"], imsi, body.speed_class)
    except SoracomNotConfiguredError:
        _not_configured_as_400()
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Line not found")
    log_audit("tenant", payload["sub"], payload["email"], "update_sim_speed_class",
              tenant_id=payload["tenant_id"], resource_type="sim", resource_id=imsi,
              detail={"speed_class": body.speed_class})
    return line
```

続けて、SORACOM側の5xx・タイムアウト等（`SoracomApiError`）を502に変換する処理も、同じStep内で追加する。

- [ ] **Step 3a: 失敗するテストを追記する**

```python
from app.services.soracom_client import SoracomApiError


def test_list_lines_soracom_failure_returns_502():
    with patch("app.routers.tenant_sim.list_lines", side_effect=SoracomApiError("SORACOM側で一時的な問題が発生しています")):
        resp = client.get("/tenant-portal/me/sim/lines", cookies=_cookies())
    assert resp.status_code == 502
    assert "一時的な問題" in resp.json()["detail"]
```

Run: `cd core-api && python -m pytest tests/test_tenant_sim_api.py -v -k soracom_failure`
Expected: `SoracomApiError`が握られずテストクライアント側に伝播する（FastAPIの既定の例外ハンドラにより500。詳細は`AssertionError: 500 != 502`）

- [ ] **Step 3b: `SoracomApiError`を502に変換する**

`tenant_sim.py`のimportに`from app.services.soracom_client import SoracomApiError`を追加し、`activate_line_endpoint`・`deactivate_line_endpoint`・`set_speed_class_endpoint`・`list_lines_endpoint`・`get_line_endpoint`の`try`ブロックに以下を追加する（`except SoracomNotConfiguredError: _not_configured_as_400()`の直後に並べる）:

```python
    except SoracomApiError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_sim_api.py -v`
Expected: 15 passed

- [ ] **Step 5: コミット**

```bash
git add core-api/app/routers/tenant_sim.py core-api/tests/test_tenant_sim_api.py
git commit -m "feat(sim): 回線一覧・詳細・activate/deactivate/速度クラス変更のAPIを追加"
```

---

### Task 10: 紐づけAPI（`/me/sim/lines/{imsi}/bind`）

**Files:**
- Modify: `core-api/app/routers/tenant_sim.py`
- Test: `core-api/tests/test_tenant_sim_api.py`（追記）

**Interfaces:**
- Consumes: Task 5の`bind_line`/`unbind_line`、`DeviceNotFoundError`/`SimAlreadyBoundError`/`DeviceAlreadyBoundError`/`BindingNotFoundError`。
- Produces: `POST /me/sim/lines/{imsi}/bind`、`DELETE /me/sim/lines/{imsi}/bind`（admin/operator）。

- [ ] **Step 1: 失敗するテストを追記する**

```python
from app.services.sim_service import BindingNotFoundError, DeviceAlreadyBoundError, DeviceNotFoundError, SimAlreadyBoundError


def test_bind_device_not_found_returns_404():
    with patch("app.routers.tenant_sim.bind_line", side_effect=DeviceNotFoundError("dev-001")):
        resp = client.post("/tenant-portal/me/sim/lines/440100000001/bind",
                           cookies=_cookies("operator"), json={"device_id": "dev-001"})
    assert resp.status_code == 404


@pytest.mark.parametrize("error", [SimAlreadyBoundError("x"), DeviceAlreadyBoundError("x")])
def test_bind_conflicts_return_409(error):
    with patch("app.routers.tenant_sim.bind_line", side_effect=error):
        resp = client.post("/tenant-portal/me/sim/lines/440100000001/bind",
                           cookies=_cookies("operator"), json={"device_id": "dev-001"})
    assert resp.status_code == 409


def test_bind_success_logs_audit():
    with patch("app.routers.tenant_sim.bind_line") as mock_bind, \
         patch("app.routers.tenant_sim.log_audit") as mock_audit:
        resp = client.post("/tenant-portal/me/sim/lines/440100000001/bind",
                           cookies=_cookies("operator"), json={"device_id": "dev-001", "iccid": "8981...01"})
    assert resp.status_code == 204
    mock_bind.assert_called_once_with(TENANT_ID, "440100000001", "dev-001", iccid="8981...01")
    assert mock_audit.call_args.args[3] == "bind_sim"


def test_unbind_not_found_returns_404():
    with patch("app.routers.tenant_sim.unbind_line", side_effect=BindingNotFoundError("x")):
        resp = client.delete("/tenant-portal/me/sim/lines/440100000001/bind", cookies=_cookies("operator"))
    assert resp.status_code == 404


def test_unbind_success():
    with patch("app.routers.tenant_sim.unbind_line") as mock_unbind, \
         patch("app.routers.tenant_sim.log_audit"):
        resp = client.delete("/tenant-portal/me/sim/lines/440100000001/bind", cookies=_cookies("operator"))
    assert resp.status_code == 204
    mock_unbind.assert_called_once_with(TENANT_ID, "440100000001")
```

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_sim_api.py -v -k bind`
Expected: `404 Not Found`

- [ ] **Step 3: `tenant_sim.py`に実装を追加する**

importを拡張: `from app.services.sim_service import (BindingNotFoundError, DeviceAlreadyBoundError, DeviceNotFoundError, SimAlreadyBoundError, bind_line, unbind_line, ...既存分...)`

ファイル末尾に追加:

```python
class BindRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=255)
    iccid: Optional[str] = None


@router.post("/lines/{imsi}/bind", status_code=status.HTTP_204_NO_CONTENT)
def bind_line_endpoint(imsi: str, body: BindRequest, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    try:
        bind_line(tenant_id, imsi, body.device_id, iccid=body.iccid)
    except DeviceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    except (SimAlreadyBoundError, DeviceAlreadyBoundError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
    log_audit("tenant", payload["sub"], payload["email"], "bind_sim",
              tenant_id=tenant_id, resource_type="sim", resource_id=imsi,
              detail={"device_id": body.device_id})


@router.delete("/lines/{imsi}/bind", status_code=status.HTTP_204_NO_CONTENT)
def unbind_line_endpoint(imsi: str, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    try:
        unbind_line(tenant_id, imsi)
    except BindingNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Binding not found")
    log_audit("tenant", payload["sub"], payload["email"], "unbind_sim",
              tenant_id=tenant_id, resource_type="sim", resource_id=imsi)
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_sim_api.py -v`
Expected: 21 passed

- [ ] **Step 5: 全体のテストを実行する**

Run: `cd core-api && python -m pytest -q`
Expected: 既存分も含め全件pass（新規追加分 約60件増）

- [ ] **Step 6: コミット**

```bash
git add core-api/app/routers/tenant_sim.py core-api/tests/test_tenant_sim_api.py
git commit -m "feat(sim): SIMとデバイスの紐づけ・解除のAPIを追加"
```

---

### Task 11: デバイス削除時に紐づけを解除する

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`
- Modify: `core-api/app/routers/tenant_devices.py`
- Test: `core-api/tests/test_tenant_devices.py`（既存ファイルに追記）
- Test: `core-api/tests/test_tenant_portal_devices.py`（既存ファイルが無い場合は確認: `ls core-api/tests/ | grep -i "portal.*device\|device.*portal"`。無ければ新規作成する）

**Interfaces:**
- Consumes: なし（`DELETE`文を1行追加するのみ）。

- [ ] **Step 1: 既存のテストファイル構成を確認する**

Run: `cd core-api && ls tests/ | grep -iE "tenant_devices|tenant_portal"`

テナントポータル側の`delete_device`ハンドラのテストが既存のどのファイルにあるか確認する（`tests/test_tenant_portal.py`等、`delete_device`または`DELETE.*me/devices`で検索: `grep -rn "delete_device\|me/devices/" tests/*.py`）。見つかったファイルに追記する。以降の手順ではそのファイルを`<PORTAL_TEST_FILE>`と呼ぶ。

- [ ] **Step 2: 失敗するテストを書く（PF側）**

`core-api/tests/test_tenant_devices.py`に追記:

```python
def test_delete_device_also_removes_its_sim_binding():
    with patch("app.routers.tenant_devices._get_active_tenant") as mock_tenant, \
         patch("app.routers.tenant_devices.engine") as mock_engine, \
         patch("app.routers.tenant_devices.forget_device"), \
         patch("app.routers.tenant_devices.kick_client"), \
         patch("app.routers.tenant_devices.ensure_sim_tables_to_tenant_schema") as mock_ensure:
        tenant = MagicMock(influxdb_org_id=None)
        mock_tenant.return_value = tenant
        conn = MagicMock()
        conn.__enter__ = lambda s: conn
        conn.__exit__ = MagicMock(return_value=False)
        row = MagicMock(device_name="センサー01")
        conn.execute.return_value.fetchone.return_value = row
        mock_engine.connect.return_value = conn

        resp = client.delete(
            f"/tenants/{TENANT_ID}/devices/dev-001",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 204
    mock_ensure.assert_called_once_with(TENANT_ID)
    sql_statements = [str(c.args[0]) for c in conn.execute.call_args_list]
    assert any("sim_bindings" in s and "DELETE" in s for s in sql_statements)
```

既存テストファイルの先頭の`TENANT_ID`・`_platform_token()`・`client`が無い場合は、同ファイル内の既存のdevice削除テストと同じものを使う（新しい変数を増やさない）。

- [ ] **Step 3: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_devices.py -v -k sim_binding`
Expected: `AttributeError` または `AssertionError`（`ensure_sim_tables_to_tenant_schema`未import）

- [ ] **Step 4: `tenant_devices.py`を修正する**

`core-api/app/routers/tenant_devices.py`のimportに追加: `from app.database import ensure_sim_tables_to_tenant_schema`

`delete_tenant_device`内の`conn.execute(text(f'DELETE FROM "{schema}".devices WHERE device_id = :did'), {"did": device_id})`の直前に追加:

```python
        ensure_sim_tables_to_tenant_schema(tenant_id_str)
        conn.execute(text(f'DELETE FROM "{schema}".sim_bindings WHERE device_id = :did'), {"did": device_id})
```

- [ ] **Step 5: 同様のテストを書き、`tenant_portal.py`の`delete_device`を修正する**

`<PORTAL_TEST_FILE>`に、Step 2と同じ要領で`delete_device`（`/tenant-portal/me/devices/{device_id}`、cookie認証）向けのテストを追加する。

`core-api/app/routers/tenant_portal.py`のimportに追加: `from app.database import ensure_sim_tables_to_tenant_schema`（既存のimport群、`add_firmware_tables_to_tenant_schema`と同じ行に追加してよい）。

`delete_device`内の`conn.execute(text(f'DELETE FROM "{schema}".devices WHERE device_id = :did'), {"did": device_id})`の直前に追加:

```python
        ensure_sim_tables_to_tenant_schema(tenant_id)
        conn.execute(text(f'DELETE FROM "{schema}".sim_bindings WHERE device_id = :did'), {"did": device_id})
```

- [ ] **Step 6: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_devices.py <PORTAL_TEST_FILE> -v -k sim_binding`
Expected: 2 passed

- [ ] **Step 7: 実DBで確認する（Dockerが動いている場合）**

適当なテナントでSIMをデバイスに紐づけたあと、そのデバイスを削除し、`sim_bindings`テーブルから該当行が消えることを`psql`で確認する。

- [ ] **Step 8: コミット**

```bash
git add core-api/app/routers/tenant_portal.py core-api/app/routers/tenant_devices.py core-api/tests/test_tenant_devices.py
git commit -m "fix(sim): デバイス削除時にsim_bindingsの紐づけも削除する"
```

---

### Task 12: PF管理者側のSORACOM連携バッジ

**Files:**
- Modify: `core-api/app/schemas/tenant.py`
- Modify: `core-api/app/routers/tenants.py`
- Modify: `platform-ui/tenant.html`
- Test: `core-api/tests/test_tenants.py`（既存ファイルに追記。無ければ`grep -rln "def get_tenant\b" tests/*.py`等で該当ファイルを特定する）

**Interfaces:**
- Consumes: Task 2の`is_configured`。
- Produces: `GET /tenants/{tenant_id}`のレスポンスに`soracom_configured: bool`を追加。

- [ ] **Step 1: 失敗するテストを書く**

`GET /tenants/{id}`を検証している既存テストファイルに追記（ファイルが見つからない場合は`core-api/tests/test_tenants_soracom_badge.py`を新規作成）:

```python
from unittest.mock import patch


def test_get_tenant_includes_soracom_configured_flag():
    with patch("app.routers.tenants.is_configured", return_value=True):
        resp = client.get(f"/tenants/{TENANT_ID}", headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.status_code == 200
    assert resp.json()["soracom_configured"] is True
```

（既存の`client`・`TENANT_ID`・`_platform_token()`が定義済みのファイルに追記する前提。新規ファイルの場合はTask 11 Step 2と同じ要領で`TestClient(app)`・`create_access_token`のヘルパーを用意する。）

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest tests/test_tenants.py -v -k soracom_configured`
Expected: `KeyError: 'soracom_configured'`

- [ ] **Step 3: `schemas/tenant.py`に`TenantDetailOut`を追加する**

`core-api/app/schemas/tenant.py`の`TenantOut`クラスの直後に追加:

```python
class TenantDetailOut(TenantOut):
    soracom_configured: bool = False
```

- [ ] **Step 4: `tenants.py`の`get_tenant`を修正する**

`core-api/app/routers/tenants.py`のimportに追加: `from app.schemas.tenant import TenantCreate, TenantOut, TenantDetailOut`（既存のimportを拡張）、`from app.services.soracom_credentials import is_configured`

`get_tenant`を次のように変更:

```python
@router.get("/{tenant_id}", response_model=TenantDetailOut)
def get_tenant(tenant_id: str, _: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        return TenantDetailOut(**TenantOut.model_validate(tenant).model_dump(), soracom_configured=is_configured(tenant_id))
```

- [ ] **Step 5: テストが通ることを確認する**

Run: `cd core-api && python -m pytest tests/test_tenants.py -v -k soracom_configured`
Expected: 1 passed

- [ ] **Step 6: `platform-ui/tenant.html`にバッジを追加する**

テナント詳細ページのヘッダー部分（テナント名表示の近く）に、既存の他のバッジ（例: ステータス表示）と同じクラスのバッジを1つ追加する。該当箇所は`grep -n "tenant.status\|x-text=\"tenant\." platform-ui/tenant.html | head -20`で特定し、見つかったバッジの直後に以下を挿入する:

```html
<span class="text-xs px-2 py-0.5 rounded font-medium"
      :class="tenant.soracom_configured ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'"
      x-text="tenant.soracom_configured ? 'SORACOM連携済み' : 'SORACOM未連携'"></span>
```

- [ ] **Step 7: CSSをビルドする**

Run: `npm run build:css`

- [ ] **Step 8: コミット**

```bash
git add core-api/app/schemas/tenant.py core-api/app/routers/tenants.py core-api/tests/test_tenants.py platform-ui/tenant.html admin-ui/static/tailwind.css
git commit -m "feat(sim): テナント詳細画面にSORACOM連携バッジを追加"
```

---

### Task 13: テナントポータルに「SIM管理」タブ（連携設定カード）を追加する

**Files:**
- Modify: `admin-ui/tenant-portal.html`

**Interfaces:**
- Consumes: Task 8のAPI（`GET/PUT/DELETE /me/sim/credentials`）。
- Produces: `tabs`配列に`sim`タブ、`activeTab === 'sim'`セクション（連携設定カードのみ。回線一覧はTask 14）。

このタスクはUIのみでcore-apiの変更を伴わないため、ブラウザでの手動確認が自動テストの代わりになる。

- [ ] **Step 1: タブ定義に「SIM管理」を追加する**

`admin-ui/tenant-portal.html`の`tabs`ゲッター（`{ id: 'alerts', label: 'アラートルール', icon: '🔔' },`の直後）に追加:

```javascript
      { id: 'sim', label: 'SIM管理', icon: '📶' },
```

- [ ] **Step 2: 連携設定カードのマークアップを追加する**

`<!-- ===== アラートルール ===== -->`の`<div x-show="activeTab === 'alerts'">`セクションの閉じタグ（この次のタブ区切りコメントの直前）の後に追加:

```html
          <!-- ===== SIM管理 ===== -->
          <div x-show="activeTab === 'sim'">
            <div class="bg-white rounded shadow-sm border border-gray-200 p-4 mb-4">
              <h3 class="text-sm font-semibold text-gray-700 mb-3">SORACOM連携</h3>
              <div x-show="simCredentialsLoading" class="text-sm text-gray-400">読み込み中...</div>
              <template x-if="!simCredentialsLoading">
                <div>
                  <div x-show="simCredentials.configured" class="flex items-center gap-3 text-sm mb-3">
                    <span class="text-green-700">連携済み（<span x-text="simCredentials.coverage === 'g' ? 'グローバル' : '日本'"></span>カバレッジ、Auth Key ID: <span x-text="simCredentials.auth_key_id_hint"></span>）</span>
                    <button x-show="isAdmin" @click="simCredentialsEditing = true" class="text-amber-600 hover:text-amber-800 text-xs">変更</button>
                    <button x-show="isAdmin" @click="deleteSimCredentials()" class="text-red-500 hover:text-red-700 text-xs">連携解除</button>
                  </div>
                  <p x-show="!simCredentials.configured && !isAdmin" class="text-sm text-gray-500">
                    SORACOM連携が未設定です。テナント管理者に設定を依頼してください。
                  </p>
                  <form x-show="isAdmin && (!simCredentials.configured || simCredentialsEditing)"
                        @submit.prevent="saveSimCredentials()" class="grid grid-cols-2 gap-3 max-w-lg">
                    <div class="col-span-2">
                      <label class="block text-xs font-medium text-gray-600 mb-1">カバレッジ</label>
                      <select x-model="simCredentialsForm.coverage" class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
                        <option value="jp">日本カバレッジ</option>
                        <option value="g">グローバルカバレッジ</option>
                      </select>
                    </div>
                    <div class="col-span-2">
                      <label class="block text-xs font-medium text-gray-600 mb-1">Auth Key ID</label>
                      <input type="text" x-model="simCredentialsForm.auth_key_id" autocomplete="off"
                             class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
                    </div>
                    <div class="col-span-2">
                      <label class="block text-xs font-medium text-gray-600 mb-1">Auth Key</label>
                      <input type="password" x-model="simCredentialsForm.auth_key" autocomplete="off"
                             class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm">
                    </div>
                    <div class="col-span-2 flex justify-end gap-2">
                      <button x-show="simCredentialsEditing" type="button" @click="simCredentialsEditing = false"
                              class="text-sm text-gray-500 px-3 py-1.5">キャンセル</button>
                      <button type="submit" :disabled="simCredentialsSaving"
                              class="bg-amber-600 text-white text-sm px-3 py-1.5 rounded disabled:opacity-50">
                        <span x-show="!simCredentialsSaving">保存</span>
                        <span x-show="simCredentialsSaving">確認中...</span>
                      </button>
                    </div>
                    <p x-show="simCredentialsError" class="col-span-2 text-xs text-red-500" x-text="simCredentialsError"></p>
                  </form>
                </div>
              </template>
            </div>
          </div>
```

- [ ] **Step 3: Alpineの状態とメソッドを追加する**

`portalApp()`の`return { ... }`内、既存の`activeTab: 'dashboard',`の近くに状態を追加:

```javascript
    simCredentials: { configured: false, coverage: 'jp', auth_key_id_hint: null },
    simCredentialsLoading: false,
    simCredentialsEditing: false,
    simCredentialsSaving: false,
    simCredentialsError: '',
    simCredentialsForm: { coverage: 'jp', auth_key_id: '', auth_key: '' },
```

メソッドは、既存の`async loadDevices() { ... }`等と同じ階層に追加:

```javascript
    async loadSimCredentials() {
      this.simCredentialsLoading = true;
      try {
        this.simCredentials = await portalFetch('GET', '/me/sim/credentials');
        this.simCredentialsForm.coverage = this.simCredentials.coverage || 'jp';
      } catch (e) {
        this.error = e.message;
      } finally {
        this.simCredentialsLoading = false;
      }
    },
    async saveSimCredentials() {
      this.simCredentialsSaving = true;
      this.simCredentialsError = '';
      try {
        await portalFetch('PUT', '/me/sim/credentials', { ...this.simCredentialsForm });
        this.simCredentialsEditing = false;
        this.simCredentialsForm.auth_key = '';
        await this.loadSimCredentials();
      } catch (e) {
        this.simCredentialsError = e.message;
      } finally {
        this.simCredentialsSaving = false;
      }
    },
    async deleteSimCredentials() {
      if (!confirm('SORACOM連携を解除しますか？紐づけは残りますが、回線の確認・操作ができなくなります。')) return;
      try {
        await portalFetch('DELETE', '/me/sim/credentials');
        await this.loadSimCredentials();
      } catch (e) {
        this.error = e.message;
      }
    },
```

- [ ] **Step 4: タブ切り替え時にデータを読み込む**

既存の`activeTab`を切り替えるUIコード（サイドバーの`@click="activeTab = t.id"`相当。`grep -n "activeTab = t.id\|activeTab = tab.id" admin-ui/tenant-portal.html`で特定）の近くで、タブ切り替え時の初期読み込みが行われている箇所（既存の`alerts`/`devices`タブ切り替え時に`loadDevices()`等を呼んでいる箇所、`grep -n "activeTab === 'alerts'.*load\|watch.*activeTab" admin-ui/tenant-portal.html`で特定）と同じパターンに倣い、`sim`タブを開いたときに`loadSimCredentials()`を呼ぶ処理を追加する（既存の仕組みが`@click`ハンドラ内にある場合はそこへ、`$watch('activeTab', ...)`等であればそこへ分岐を1行追加する）。

- [ ] **Step 5: CSSをビルドする**

Run: `npm run build:css`

- [ ] **Step 6: ブラウザで確認する（Dockerが動いている場合）**

テナントポータルにログインし、「SIM管理」タブが表示されること、未連携時はadminに入力フォームが、operatorには案内文が出ることを確認する。実際のSORACOM認証情報を持っていない場合は、`PUT`時の`verify_credentials`呼び出しが失敗して422になることを確認する（Task 8の挙動どおり）。

- [ ] **Step 7: コミット**

```bash
git add admin-ui/tenant-portal.html admin-ui/static/tailwind.css
git commit -m "feat(sim): テナントポータルにSIM管理タブ(SORACOM連携設定)を追加"
```

---

### Task 14: SIM管理タブに回線一覧・操作UIを追加する

**Files:**
- Modify: `admin-ui/tenant-portal.html`

**Interfaces:**
- Consumes: Task 9・10のAPI（`GET /me/sim/lines`、activate/deactivate/speed-class、bind/unbind）、Task 13の`sim`タブ。

- [ ] **Step 1: 回線一覧のマークアップをTask 13の連携設定カードの直後に追加する**

```html
            <div class="bg-white rounded shadow-sm border border-gray-200">
              <div class="flex items-center gap-2 p-3 border-b border-gray-100 flex-wrap">
                <select x-model="simFilter.status" @change="reloadSimLines()" class="text-xs border border-gray-300 rounded px-2 py-1">
                  <option value="">状態: すべて</option>
                  <option value="active">active</option>
                  <option value="inactive">inactive</option>
                  <option value="ready">ready</option>
                </select>
                <select x-model="simFilter.bound" @change="reloadSimLines()" class="text-xs border border-gray-300 rounded px-2 py-1">
                  <option value="">紐づけ: すべて</option>
                  <option value="true">紐づけ済みのみ</option>
                  <option value="false">未紐づけのみ</option>
                </select>
                <input type="text" x-model="simFilter.tag_value" @keydown.enter="reloadSimLines()"
                       placeholder="タグ値で検索" class="text-xs border border-gray-300 rounded px-2 py-1">
                <button @click="reloadSimLines()" class="text-xs text-amber-600 hover:text-amber-800">検索</button>
              </div>
              <div x-show="!simLinesLoading && simLines.length === 0" class="py-8 text-center text-gray-400 text-sm">
                回線が見つかりません（SORACOM連携が未設定の場合は上の連携設定を確認してください）
              </div>
              <div class="overflow-x-auto">
                <table x-show="simLines.length > 0" class="w-full text-sm">
                  <thead class="bg-gray-50 border-b border-gray-100">
                    <tr>
                      <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">IMSI</th>
                      <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">状態</th>
                      <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">速度クラス</th>
                      <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">紐づけデバイス</th>
                      <th class="px-4 py-3"></th>
                    </tr>
                  </thead>
                  <tbody class="divide-y divide-gray-100">
                    <template x-for="line in simLines" :key="line.imsi">
                      <tr>
                        <td class="px-4 py-3 font-mono text-xs" x-text="line.imsi"></td>
                        <td class="px-4 py-3">
                          <span class="text-xs px-2 py-0.5 rounded font-medium"
                                :class="line.status === 'active' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'"
                                x-text="line.status"></span>
                        </td>
                        <td class="px-4 py-3">
                          <select x-show="canEdit" :value="line.speedClass"
                                  @change="changeSimSpeedClass(line, $event.target.value)"
                                  class="text-xs border border-gray-300 rounded px-1.5 py-1">
                            <option :value="line.speedClass" x-text="line.speedClass" selected></option>
                            <template x-for="sc in simSpeedClassOptions" :key="sc">
                              <option :value="sc" x-text="sc" x-show="sc !== line.speedClass"></option>
                            </template>
                          </select>
                          <span x-show="!canEdit" x-text="line.speedClass"></span>
                        </td>
                        <td class="px-4 py-3">
                          <span x-show="line.bound_device_id" x-text="line.bound_device_name"></span>
                          <button x-show="canEdit && !line.bound_device_id" @click="openSimBindModal(line)"
                                  class="text-amber-600 hover:text-amber-800 text-xs">紐づける</button>
                        </td>
                        <td class="px-4 py-3 text-right space-x-2">
                          <button x-show="canEdit && line.status !== 'active'" @click="activateSimLine(line)"
                                  class="text-green-600 hover:text-green-800 text-xs">有効化</button>
                          <button x-show="canEdit && line.status === 'active'" @click="deactivateSimLine(line)"
                                  class="text-gray-500 hover:text-gray-700 text-xs">停止</button>
                          <button x-show="canEdit && line.bound_device_id" @click="unbindSimLine(line)"
                                  class="text-red-500 hover:text-red-700 text-xs">紐づけ解除</button>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
              <div class="p-3 text-center">
                <button x-show="simNextCursor" @click="loadMoreSimLines()" :disabled="simLinesLoading"
                        class="text-sm text-amber-600 hover:text-amber-800 disabled:text-amber-200">
                  <span x-show="!simLinesLoading">さらに読み込む</span>
                  <span x-show="simLinesLoading">読み込み中...</span>
                </button>
              </div>
            </div>

            <!-- SIM紐づけモーダル -->
            <div x-show="simBindModalOpen" class="fixed inset-0 bg-black bg-opacity-30 flex items-center justify-center z-50 p-4">
              <div class="bg-white rounded p-6 w-full max-w-md shadow-xl">
                <h3 class="font-medium text-gray-800 mb-3">デバイスに紐づける</h3>
                <p class="text-xs text-gray-500 mb-2">IMSI: <span class="font-mono" x-text="simBindTarget && simBindTarget.imsi"></span></p>
                <select x-model="simBindDeviceId" class="w-full border border-gray-300 rounded px-2 py-1.5 text-sm mb-3">
                  <option value="">デバイスを選択</option>
                  <template x-for="d in devices" :key="d.id">
                    <option :value="d.device_id" x-text="d.device_name || d.device_id"></option>
                  </template>
                </select>
                <p x-show="simBindError" class="text-xs text-red-500 mb-2" x-text="simBindError"></p>
                <div class="flex justify-end gap-2">
                  <button @click="simBindModalOpen = false" class="text-sm text-gray-500 px-3 py-1.5">キャンセル</button>
                  <button @click="submitSimBind()" :disabled="!simBindDeviceId"
                          class="bg-amber-600 disabled:bg-amber-200 text-white text-sm px-3 py-1.5 rounded">紐づける</button>
                </div>
              </div>
            </div>
```

- [ ] **Step 2: 状態を追加する**

Task 13で追加した`simCredentials...`の状態の直後に追加:

```javascript
    simLines: [],
    simLinesLoading: false,
    simNextCursor: null,
    simFilter: { status: '', bound: '', tag_value: '' },
    simSpeedClassOptions: ['s1.minimum', 's1.slow', 's1.standard', 's1.fast', 's1.4xfast', 's1.8xfast'],
    simBindModalOpen: false,
    simBindTarget: null,
    simBindDeviceId: '',
    simBindError: '',
```

- [ ] **Step 3: メソッドを追加する**

Task 13の`deleteSimCredentials()`の直後に追加:

```javascript
    async reloadSimLines() {
      this.simLines = [];
      this.simNextCursor = null;
      await this.loadMoreSimLines();
    },
    async loadMoreSimLines() {
      this.simLinesLoading = true;
      try {
        const params = new URLSearchParams();
        if (this.simFilter.status) params.set('status', this.simFilter.status);
        if (this.simFilter.bound) params.set('bound', this.simFilter.bound);
        if (this.simFilter.tag_value) { params.set('tag_name', 'name'); params.set('tag_value', this.simFilter.tag_value); }
        if (this.simNextCursor) params.set('cursor', this.simNextCursor);
        const result = await portalFetch('GET', `/me/sim/lines?${params.toString()}`);
        this.simLines = this.simLines.concat(result.items);
        this.simNextCursor = result.next_cursor;
      } catch (e) {
        this.error = e.message;
      } finally {
        this.simLinesLoading = false;
      }
    },
    async activateSimLine(line) {
      try {
        const updated = await portalFetch('POST', `/me/sim/lines/${line.imsi}/activate`);
        Object.assign(line, updated);
      } catch (e) { this.error = e.message; }
    },
    async deactivateSimLine(line) {
      try {
        const updated = await portalFetch('POST', `/me/sim/lines/${line.imsi}/deactivate`);
        Object.assign(line, updated);
      } catch (e) { this.error = e.message; }
    },
    async changeSimSpeedClass(line, speedClass) {
      try {
        const updated = await portalFetch('POST', `/me/sim/lines/${line.imsi}/speed-class`, { speed_class: speedClass });
        Object.assign(line, updated);
      } catch (e) { this.error = e.message; }
    },
    openSimBindModal(line) {
      this.simBindTarget = line;
      this.simBindDeviceId = '';
      this.simBindError = '';
      this.simBindModalOpen = true;
    },
    async submitSimBind() {
      this.simBindError = '';
      try {
        await portalFetch('POST', `/me/sim/lines/${this.simBindTarget.imsi}/bind`,
                          { device_id: this.simBindDeviceId, iccid: this.simBindTarget.iccid || null });
        this.simBindModalOpen = false;
        await this.reloadSimLines();
      } catch (e) {
        this.simBindError = e.message;
      }
    },
    async unbindSimLine(line) {
      if (!confirm('紐づけを解除しますか？')) return;
      try {
        await portalFetch('DELETE', `/me/sim/lines/${line.imsi}/bind`);
        await this.reloadSimLines();
      } catch (e) { this.error = e.message; }
    },
```

- [ ] **Step 4: タブを開いたときに一覧も読み込む**

Task 13 Step 4で追加した「`sim`タブを開いたら`loadSimCredentials()`」の呼び出しに、`this.reloadSimLines();`を並べて追加する（連携設定・一覧を同時に読み込む）。

- [ ] **Step 5: CSSをビルドする**

Run: `npm run build:css`

- [ ] **Step 6: ブラウザで確認する（Dockerが動いている場合）**

SORACOM連携設定済みのテナントで、一覧表示・フィルタ・「さらに読み込む」・有効化/停止・速度クラス変更・紐づけ/紐づけ解除を一通り確認する。実際のSORACOM契約が手元に無い場合は、`app/services/sim_service.list_lines`等を一時的にモックして確認してもよい。

- [ ] **Step 7: コミット**

```bash
git add admin-ui/tenant-portal.html admin-ui/static/tailwind.css
git commit -m "feat(sim): SIM管理タブに回線一覧・有効化/停止・速度クラス変更・紐づけUIを追加"
```

---

### Task 15: デバイス一覧タブにSIMバッジを追加する

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`（`list_devices`に紐づけ情報を追加）
- Modify: `admin-ui/tenant-portal.html`
- Test: `<PORTAL_TEST_FILE>`（Task 11で特定したファイル）

**Interfaces:**
- Consumes: Task 5の`get_bindings_by_imsi`は使わない（デバイス側から見るのでキーが逆になるため、`device_id`で直接JOINするSQLを書く）。
- Produces: `GET /me/devices`の各要素に`sim_imsi: str | None`を追加。

- [ ] **Step 1: 失敗するテストを書く**

`<PORTAL_TEST_FILE>`に追記:

```python
def test_list_devices_includes_bound_sim_imsi():
    with patch("app.routers.tenant_portal.engine") as mock_engine:
        conn = MagicMock()
        conn.__enter__ = lambda s: conn
        conn.__exit__ = MagicMock(return_value=False)
        row = MagicMock(id="d1", device_id="dev-001", device_name="センサー01", connection_status="online",
                        last_seen_at=None, fw_version=None, cert_not_after=None, created_at=None,
                        group_id=None, sim_imsi="440100000001")
        conn.execute.return_value.fetchall.return_value = [row]
        mock_engine.connect.return_value = conn

        resp = client.get("/tenant-portal/me/devices", cookies=_tenant_cookies())
    assert resp.status_code == 200
    assert resp.json()[0]["sim_imsi"] == "440100000001"
```

（`_tenant_cookies()`は既存のヘルパーを使う。無い場合はTask 11で追加した認証ヘルパーに合わせる。）

- [ ] **Step 2: テストが失敗することを確認する**

Run: `cd core-api && python -m pytest <PORTAL_TEST_FILE> -v -k sim_imsi`
Expected: `KeyError: 'sim_imsi'`

- [ ] **Step 3: `list_devices`を修正する**

`core-api/app/routers/tenant_portal.py`の`list_devices`のSQLを、`sim_bindings`との`LEFT JOIN`に変更する（`sim_bindings`テーブルが無いテナントでも落ちないよう、事前に`ensure_sim_tables_to_tenant_schema(tenant_id)`を呼ぶ）:

```python
@router.get("/me/devices")
def list_devices(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    ensure_sim_tables_to_tenant_schema(tenant_id)
    with engine.connect() as conn:
        rows = conn.execute(text(f'''
            SELECT d.id, d.device_id, d.device_name, d.connection_status, d.last_seen_at,
                   d.fw_version, d.cert_not_after, d.created_at, d.group_id, b.imsi AS sim_imsi
            FROM "{schema}".devices d
            LEFT JOIN "{schema}".sim_bindings b ON b.device_id = d.device_id
            ORDER BY d.created_at DESC
            LIMIT 1000
        ''')).fetchall()
    return [
        {
            "id": str(r.id),
            "device_id": r.device_id,
            "device_name": r.device_name or r.device_id,
            "connection_status": r.connection_status,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            "fw_version": r.fw_version,
            "cert_not_after": r.cert_not_after.isoformat() if r.cert_not_after else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "group_id": str(r.group_id) if r.group_id else None,
            "sim_imsi": r.sim_imsi,
        }
        for r in rows
    ]
```

- [ ] **Step 4: テストが通ることを確認する**

Run: `cd core-api && python -m pytest <PORTAL_TEST_FILE> -v -k sim_imsi`
Expected: 1 passed

- [ ] **Step 5: デバイス一覧テーブルにバッジ列を追加する**

`admin-ui/tenant-portal.html`のデバイス一覧テーブル、`<th class="px-4 py-3"></th>`（操作列、`削除`ボタンの列見出し）の直前に追加:

```html
                        <th class="text-left px-4 py-3 text-xs font-medium text-gray-500">SIM</th>
```

対応するデータ行、`<td class="px-4 py-3 text-right">`（削除ボタンのセル）の直前に追加:

```html
                          <td class="px-4 py-3">
                            <button x-show="d.sim_imsi" @click="activeTab = 'sim'; simFilter.tag_value = ''; $nextTick(() => reloadSimLines())"
                                    class="text-xs px-2 py-0.5 rounded bg-blue-100 text-blue-700">📶 紐づけ済み</button>
                            <span x-show="!d.sim_imsi" class="text-xs text-gray-300">-</span>
                          </td>
```

- [ ] **Step 6: CSSをビルドする**

Run: `npm run build:css`

- [ ] **Step 7: ブラウザで確認する（Dockerが動いている場合）**

デバイス一覧でSIM紐づけ済みのデバイスにバッジが表示され、クリックでSIM管理タブに切り替わることを確認する。

- [ ] **Step 8: コミット**

```bash
git add core-api/app/routers/tenant_portal.py admin-ui/tenant-portal.html admin-ui/static/tailwind.css <PORTAL_TEST_FILE>
git commit -m "feat(sim): デバイス一覧にSIM紐づけバッジを追加"
```

---

### Task 16: インストーラへの`SECRETS_ENCRYPTION_KEY`配線

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `install-aws.sh`
- Modify: `install-mac.sh`
- Modify: `install-ubuntu.sh`

`scripts/setup.sh`は既存の`.env`を読み込むだけで新規シークレットを生成しないため、変更しない（仕様書の記載から外れるが、実際のファイル内容を確認した結果の判断）。

- [ ] **Step 1: `docker-compose.yml`に環境変数を追加する**

`docker-compose.yml`のcore-apiサービスの環境変数、`STEP_CA_PASSWORD: "${STEP_CA_PASSWORD}"`の直後に追加:

```yaml
      SECRETS_ENCRYPTION_KEY: "${SECRETS_ENCRYPTION_KEY}"
```

- [ ] **Step 2: `.env.example`に追加する**

`.env.example`の`STEP_CA_PASSWORD=changeme_ca_password`の直後に追加:

```
# SORACOM認証情報など、復号して使う必要がある秘密情報の暗号化に使う鍵(32文字以上、任意の文字列でよい)
# Generate with: openssl rand -hex 32
SECRETS_ENCRYPTION_KEY=changeme_secrets_encryption_key_min_32_characters_replace_in_prod
```

- [ ] **Step 3: `install-aws.sh`を修正する**

`STEP_PASS=$(rand_hex 20)`の直後に追加:

```bash
    SECRETS_ENCRYPTION_KEY=$(rand_hex 32)
```

`sed`コマンドの`-e "s|STEP_CA_PASSWORD=.*|STEP_CA_PASSWORD=${STEP_PASS}|" \`の直後に追加:

```bash
        -e "s|SECRETS_ENCRYPTION_KEY=.*|SECRETS_ENCRYPTION_KEY=${SECRETS_ENCRYPTION_KEY}|" \
```

- [ ] **Step 4: `install-mac.sh`・`install-ubuntu.sh`に同じ変更を加える**

それぞれのファイルで、`JWT_SECRET=$(rand_hex 32)`の近くに`SECRETS_ENCRYPTION_KEY=$(rand_hex 32)`を追加し、`sed`の`JWT_SECRET=.*`の行の近くに`SECRETS_ENCRYPTION_KEY=.*`の置換行を追加する（Step 3と同じパターン）。

- [ ] **Step 5: 構文チェック**

Run: `bash -n install-aws.sh && bash -n install-mac.sh && bash -n install-ubuntu.sh && echo OK`
Expected: `OK`

- [ ] **Step 6: Dockerが動いている場合、`.env`に手動で追記して疎通確認する**

既存の`.env`に`SECRETS_ENCRYPTION_KEY=$(openssl rand -hex 32)`を追記し、`docker compose up -d --build core-api`してエラーなく起動することを確認する（`secrets_encryption_key`が未設定だと`Settings()`のコンストラクタで起動が落ちるはずなので、先に「追記前は起動に失敗する」ことも確認するとよい）。

- [ ] **Step 7: コミット**

```bash
git add docker-compose.yml .env.example install-aws.sh install-mac.sh install-ubuntu.sh
git commit -m "chore(sim): SECRETS_ENCRYPTION_KEYの生成・配線をインストーラに追加"
```

---

### Task 17: ドキュメント更新

**Files:**
- Modify: `docs/design.html`
- Modify: `CLAUDE.md`

- [ ] **Step 1: `docs/design.html`のサービス一覧・データモデル節に追記する**

既存の`core-api`の説明テーブル行、またはテナント機能の一覧節（`grep -n "ファームウェア配信\|デバイスグループ" docs/design.html`で該当箇所を特定）に、SORACOM回線管理機能の1行を追加する: 「SORACOM連携によるテナント単位の回線管理（一覧・有効化/停止・速度クラス変更・デバイス紐づけ）」。

- [ ] **Step 2: `CLAUDE.md`に追記する**

`core-api`の説明行、またはセキュリティ実装済みリストの近くに1行追加:

```markdown
- **SORACOM回線管理** — テナント単位でSORACOM APIと連携（`services/soracom_client.py`）。認証情報(Auth Key)は`services/crypto.py`で暗号化して`tenant_soracom_credentials`に保存。SIMとデバイスの紐づけはテナントスキーマの`sim_bindings`（1 SIM : 1デバイス）
```

- [ ] **Step 3: コミット**

```bash
git add docs/design.html CLAUDE.md
git commit -m "docs: SORACOM回線管理機能のドキュメントを追加"
```

---

## Execution Handoff

全17タスクを書き終えたら、このファイルを人間のパートナーに提示し、実行方法（Subagent-driven / Native）を選んでもらう（本タスク自体はプラン作成のみで、実行には着手しない）。
