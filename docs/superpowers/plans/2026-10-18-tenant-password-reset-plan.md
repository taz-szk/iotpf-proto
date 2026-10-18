# テナント管理者パスワード再設定機能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナントユーザーが「テナントスラッグ・メールアドレス」からパスワード再設定メールを受け取り、リンク経由で新しいパスワードを設定できるようにする。

**Architecture:** 既存のテナントセルフサインアップ機能(トークン付きメールリンク)と同じパターンを踏襲する。新規の`password_reset_tokens`テーブル(publicスキーマ)にトークンを保持し、`tenant_auth.py`に2本のエンドポイントを追加する。メール送信は`BackgroundTasks`で非同期化し、レスポンス内容・速度の両方でテナント/メールの存在を外部に漏らさない。

**Tech Stack:** FastAPI, SQLAlchemy(`SessionLocal`/`engine`), 既存の`verify_recaptcha`・`is_rate_limited`・`validate_password`・`hash_password`/`verify_password`サービス、Alpine.js(フロントエンド)。

**Spec:** `docs/superpowers/specs/2026-10-18-tenant-password-reset-design.md`

## Global Constraints

- 新しいパスワードのポリシーは既存の`validate_password`(12文字以上・72バイト以下)をそのまま使う
- 再設定トークンの有効期限は1時間
- reCAPTCHA v3は既存の`verify_recaptcha`・既存の`RECAPTCHA_SITE_KEY`/`RECAPTCHA_SECRET_KEY`をそのまま再利用する(新しい設定値は追加しない)
- `POST /tenant-auth/forgot-password`は、テナント・ユーザーが見つかったかどうか、reCAPTCHAが通ったかどうかに関わらず、常に`200 {"status": "requested"}`を返す
- 同エンドポイントのレート制限は、既存の`is_rate_limited`/`record_failure`(`core-api/app/services/rate_limiter.py`)を使うが、**リクエストのたびに`record_failure`を呼ぶ**(既存のログイン/サインアップの「失敗時のみ記録」とは異なり、全リクエストを数える。429の出方の違いで存在確認に使われないようにするため)。キーは`f"tenant_password_reset:{ip}"`
- メール送信は`BackgroundTasks.add_task(...)`で非同期化し、送信失敗はログ出力のみ(クライアントへは伝播させない)。パターンは`core-api/app/routers/tenants.py`の`delete_tenant`の`background_tasks.add_task(teardown_tenant, ...)`に合わせる
- パスワード更新後は`forget_session(tenant_id, user_id)`(`core-api/app/services/tenant_session.py`)を呼ぶ

## Review Focus

- 存在しないテナントスラッグ、または存在するが登録されていないメールアドレスで`POST /tenant-auth/forgot-password`を呼んだ場合も、見つかった場合と**完全に同じ**レスポンスボディ・ステータスコードが返ること(存在確認に使われないこと)
- 期限切れのトークンで`POST /tenant-auth/reset-password`を呼んだ場合、410を返し、かつそのトークン行を削除すること(期限切れトークンが居座らない)
- トークン発行後にテナントが停止(`status != "active"`)、またはユーザーが無効化(`is_active = FALSE`)された場合、`POST /tenant-auth/reset-password`は404を返し、パスワードを更新しないこと
- 一度使用済み(削除済み)のトークンで再度`POST /tenant-auth/reset-password`を呼んだ場合、404を返すこと(トークンの使い捨て)
- 同一ユーザーに対して`POST /tenant-auth/forgot-password`を複数回呼んだ場合、古い未使用トークンは新しいトークンの発行時に削除され、古いトークンではもう再設定できないこと

---

### Task 1: `PasswordResetToken`モデルとマイグレーション

**Files:**
- Modify: `core-api/app/models/public.py`(末尾に`PasswordResetToken`クラスを追加)
- Modify: `core-api/app/database.py`(末尾付近に`migrate_create_password_reset_tokens()`を追加、既存の`migrate_create_tenant_signup_requests()`と同じ`CREATE TABLE IF NOT EXISTS`パターン)
- Modify: `core-api/app/main.py`(インポート行と起動時マイグレーションタプルの両方に`migrate_create_password_reset_tokens`を追加)
- Test: `core-api/tests/test_password_reset_models.py`

**Interfaces:**
- Produces: `PasswordResetToken`(`id: UUID`, `tenant_id: UUID`, `user_id: UUID`, `email: str`, `token: str`, `created_at`, `expires_at`)
- Produces: `migrate_create_password_reset_tokens() -> None`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_password_reset_models.py
import uuid
from datetime import datetime, timedelta, timezone
from app.models.public import PasswordResetToken

def test_password_reset_token_fields():
    t = PasswordResetToken(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), user_id=uuid.uuid4(),
        email="user@example.com", token="tok123",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert t.email == "user@example.com"
    assert t.token == "tok123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_password_reset_models.py -v`
Expected: FAIL with `ImportError` または `AttributeError`(`PasswordResetToken`が未定義)

- [ ] **Step 3: `core-api/app/models/public.py`に`PasswordResetToken`クラスを追加**

`TenantSignupRequest`クラスの直後に追加。カラム: `id: UUID(as_uuid=True), primary_key=True, default=uuid.uuid4`、`tenant_id: UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False`、`user_id: UUID(as_uuid=True), nullable=False`(テナントスキーマ側の`users.id`。publicスキーマから外部キーは張れない)、`email: String(255), nullable=False`、`token: String(64), nullable=False, unique=True`、`created_at: DateTime(timezone=True), server_default=func.now()`、`expires_at: DateTime(timezone=True), nullable=False`。`__tablename__ = "password_reset_tokens"`

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_password_reset_models.py -v`
Expected: PASS

- [ ] **Step 5: `migrate_create_password_reset_tokens()`を`core-api/app/database.py`に追加**

既存の`migrate_create_tenant_signup_requests()`をコピーして`CREATE TABLE IF NOT EXISTS password_reset_tokens (...)`に書き換える(カラムはTask 1 Step 3と同じ、`token`列に`UNIQUE`制約)。`core-api/app/main.py`のインポート行と起動時マイグレーションタプルの両方に`migrate_create_password_reset_tokens`を追加する。

- [ ] **Step 6: Commit**

```bash
git add core-api/app/models/public.py core-api/app/database.py core-api/app/main.py core-api/tests/test_password_reset_models.py
git commit -m "feat(password-reset): password_reset_tokensテーブルを追加"
```

---

### Task 2: 再設定メール送信関数

**Files:**
- Modify: `core-api/app/services/mailer.py`
- Test: `core-api/tests/test_mailer_password_reset.py`

**Interfaces:**
- Consumes: なし(新規追加)
- Produces: `send_password_reset_email(to_email: str, tenant_name: str, reset_url: str) -> None`(送信失敗は例外を伝播させる。呼び出し元[Task 3]がBackgroundTasks内で捕捉してログに残す)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_mailer_password_reset.py
from unittest.mock import patch, MagicMock
import pytest
from app.services.mailer import send_password_reset_email

def test_send_password_reset_email_sends_with_link():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = instance
        send_password_reset_email(
            "user@example.com", "Acme Corp", "https://example.local/admin/reset-password.html?token=abc",
        )
    assert instance.sendmail.called
    args = instance.sendmail.call_args[0]
    assert "https://example.local/admin/reset-password.html?token=abc" in args[2]

def test_send_password_reset_email_raises_on_failure():
    with patch("app.services.mailer.smtplib.SMTP", side_effect=ConnectionError("boom")):
        with pytest.raises(ConnectionError):
            send_password_reset_email("user@example.com", "Acme Corp", "https://x/reset?token=t")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_mailer_password_reset.py -v`
Expected: FAIL with `ImportError`(`send_password_reset_email`が未定義)

- [ ] **Step 3: `send_password_reset_email`を`core-api/app/services/mailer.py`に追加**

既存の`send_signup_verification_email`と同じ構造(`MIMEMultipart`、英語本文、`smtplib.SMTP`で送信、例外は伝播させる)。件名は`f"[IoT Platform] Password reset for {tenant_name_clean}"`、本文はリンクと「1時間以内に使うこと」の注記。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_mailer_password_reset.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/mailer.py core-api/tests/test_mailer_password_reset.py
git commit -m "feat(password-reset): 再設定メール送信関数を追加"
```

---

### Task 3: `POST /tenant-auth/forgot-password`

**Files:**
- Modify: `core-api/app/routers/tenant_auth.py`
- Test: `core-api/tests/test_tenant_auth_forgot_password.py`

**Interfaces:**
- Consumes: `PasswordResetToken`(Task 1)、`send_password_reset_email`(Task 2)、`verify_recaptcha`(既存 `app.services.recaptcha`)、`is_rate_limited`/`record_failure`(既存)、`_equalize_timing`(同ファイル既存の関数)
- Produces: `POST /tenant-auth/forgot-password`。リクエストボディ`{tenant_slug: str, email: str, recaptcha_token: str}`。レスポンスは常に`200 {"status": "requested"}`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenant_auth_forgot_password.py
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.rate_limiter import clear_failures

client = TestClient(app)

def _body(slug="acme", email="user@acme.com"):
    return {"tenant_slug": slug, "email": email, "recaptcha_token": "good-token"}

def test_forgot_password_returns_same_response_when_tenant_not_found():
    clear_failures("tenant_password_reset:testclient")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=True), \
         patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}

def test_forgot_password_returns_same_response_when_user_not_found():
    clear_failures("tenant_password_reset:testclient")
    tenant = MagicMock(id="11111111-1111-1111-1111-111111111111", name="Acme", status="active")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=True), \
         patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = tenant
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}

def test_forgot_password_creates_token_and_queues_email_when_found():
    clear_failures("tenant_password_reset:testclient")
    tenant = MagicMock(id="11111111-1111-1111-1111-111111111111", name="Acme", status="active")
    row = MagicMock(id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", email="user@acme.com")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=True), \
         patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth.BackgroundTasks.add_task") as mock_add_task:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [tenant, None]
        # 1回目: Tenant検索、2回目: 既存PasswordResetTokenの検索(無し)
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = row
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}
    assert db.add.called
    assert mock_add_task.called

def test_forgot_password_low_recaptcha_score_returns_same_response():
    clear_failures("tenant_password_reset:testclient")
    with patch("app.routers.tenant_auth.verify_recaptcha", return_value=False):
        resp = client.post("/tenant-auth/forgot-password", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "requested"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenant_auth_forgot_password.py -v`
Expected: FAIL(`404 Not Found`、エンドポイント未定義)

- [ ] **Step 3: `POST /tenant-auth/forgot-password`を`core-api/app/routers/tenant_auth.py`に追加**

```python
class ForgotPasswordRequest(BaseModel):
    tenant_slug: str
    email: EmailStr
    recaptcha_token: str

@router.post("/forgot-password")
def forgot_password(req: ForgotPasswordRequest, request: Request, background_tasks: BackgroundTasks):
    ...
```

処理順序: (1) `ip = request.client.host if request.client else "unknown"`、`rate_key = f"tenant_password_reset:{ip}"`、`record_failure(rate_key)`を**無条件に**呼ぶ(Global Constraints参照)。`is_rate_limited(rate_key)`が真なら429。(2) `verify_recaptcha(req.recaptcha_token, ip)`が偽でも後続処理をスキップして`{"status": "requested"}`を返す(下記(6)と同じ早期リターン)。(3) `Tenant.slug == req.tenant_slug and Tenant.status == "active"`で検索、無ければ(6)へ。(4) 該当テナントスキーマの`users`テーブルを既存ログインと同じ`SELECT id, email FROM "{schema}".users WHERE email = :email AND is_active = TRUE`で検索、無ければ(6)へ。(5) 見つかった場合: 同じ`user_id`の既存`PasswordResetToken`を削除してから新規作成(`token=secrets.token_urlsafe(32)`、`expires_at=now+timedelta(hours=1)`)、commit。`reset_url = f"https://{settings.platform_domain}/admin/reset-password.html?token={token}"`を組み立て、`background_tasks.add_task(_send_password_reset_email_safe, req.email, tenant.name, reset_url)`で送信をキューに入れる(下記のラッパー関数経由、送信失敗を内部で捕捉してログ出力するため)。(6) 常に`return {"status": "requested"}`。

送信失敗を握り込むラッパー: `_send_password_reset_email_safe(to_email, tenant_name, reset_url)`は`send_password_reset_email`を呼び、例外は`print(f"...")`でログに残すだけ(クライアントは既にレスポンスを受け取った後なので伝播先が無い)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenant_auth_forgot_password.py -v`
Expected: PASS

- [ ] **Step 5: Run full backend test suite**

Run: `cd core-api && python -m pytest -q`
Expected: 全件PASS(既存テストに影響しないこと)

- [ ] **Step 6: Commit**

```bash
git add core-api/app/routers/tenant_auth.py core-api/tests/test_tenant_auth_forgot_password.py
git commit -m "feat(password-reset): POST /tenant-auth/forgot-password を追加"
```

---

### Task 4: `POST /tenant-auth/reset-password`

**Files:**
- Modify: `core-api/app/routers/tenant_auth.py`
- Test: `core-api/tests/test_tenant_auth_reset_password.py`

**Interfaces:**
- Consumes: `PasswordResetToken`(Task 1)、`validate_password`/`hash_password`(既存)、`forget_session`(既存 `app.services.tenant_session`)
- Produces: `POST /tenant-auth/reset-password`。リクエストボディ`{token: str, new_password: str}`。成功時`204`。トークン不正/期限切れ/テナントまたはユーザーが無効化されている場合は`404`、期限切れは`410`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenant_auth_reset_password.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _token_row(expires_delta=timedelta(hours=1), tenant_active=True, user_active=True):
    token_row = MagicMock(
        id="r1", tenant_id="11111111-1111-1111-1111-111111111111",
        user_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", email="user@acme.com",
        expires_at=datetime.now(timezone.utc) + expires_delta,
    )
    tenant = MagicMock(id=token_row.tenant_id, status="active" if tenant_active else "suspended")
    user_row = MagicMock() if user_active else None
    return token_row, tenant, user_row

def test_reset_password_rejects_unknown_token():
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = None
        resp = client.post("/tenant-auth/reset-password", json={"token": "bad", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 404

def test_reset_password_rejects_expired_token():
    token_row, tenant, user_row = _token_row(expires_delta=timedelta(hours=-1))
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.return_value = token_row
        resp = client.post("/tenant-auth/reset-password", json={"token": "tok", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 410
    assert db.delete.called

def test_reset_password_rejects_when_tenant_inactive():
    token_row, tenant, user_row = _token_row(tenant_active=False)
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [token_row, tenant]
        resp = client.post("/tenant-auth/reset-password", json={"token": "tok", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 404
    assert db.delete.called

def test_reset_password_rejects_when_user_inactive():
    token_row, tenant, _ = _token_row(user_active=False)
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [token_row, tenant]
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = None
        resp = client.post("/tenant-auth/reset-password", json={"token": "tok", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 404
    assert db.delete.called

def test_reset_password_updates_hash_and_deletes_token():
    token_row, tenant, _ = _token_row()
    with patch("app.routers.tenant_auth.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_auth.engine") as mock_engine, \
         patch("app.routers.tenant_auth.forget_session") as mock_forget:
        db = mock_sl.return_value.__enter__.return_value
        db.query.return_value.filter.return_value.first.side_effect = [token_row, tenant]
        mock_engine.connect.return_value.__enter__.return_value.execute.return_value.fetchone.return_value = MagicMock()
        resp = client.post("/tenant-auth/reset-password", json={"token": "tok", "new_password": "correct-horse-battery-staple"})
    assert resp.status_code == 204
    assert db.delete.called
    assert mock_forget.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenant_auth_reset_password.py -v`
Expected: FAIL(`404 Not Found`、エンドポイント未定義)

- [ ] **Step 3: `POST /tenant-auth/reset-password`を`core-api/app/routers/tenant_auth.py`に追加**

```python
class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(req: ResetPasswordRequest):
    ...
```

処理順序: (1) `PasswordResetToken`を`token == req.token`で検索、無ければ404。(2) `expires_at`が過去なら、トークン削除・commitの上で410。(3) `Tenant`を`id == token_row.tenant_id`で検索、無いか`status != "active"`ならトークン削除・commitの上で404。(4) 該当テナントスキーマの`users`テーブルを`SELECT id FROM "{schema}".users WHERE id = :uid AND is_active = TRUE`で検索、無ければトークン削除・commitの上で404。(5) `validate_password(req.new_password, token_row.email)`。(6) `UPDATE ... SET password_hash = :hash WHERE id = :uid`(既存の`change_password`と同じ`engine.connect()`パターン)。(7) トークン削除・commit。(8) `forget_session(str(token_row.tenant_id), str(token_row.user_id))`。(9) `log_audit("tenant", str(token_row.user_id), token_row.email, "password_reset", tenant_id=str(token_row.tenant_id))`(既存の`log_audit`呼び出しパターンに合わせる)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenant_auth_reset_password.py -v`
Expected: PASS

- [ ] **Step 5: Run full backend test suite**

Run: `cd core-api && python -m pytest -q`
Expected: 全件PASS

- [ ] **Step 6: Commit**

```bash
git add core-api/app/routers/tenant_auth.py core-api/tests/test_tenant_auth_reset_password.py
git commit -m "feat(password-reset): POST /tenant-auth/reset-password を追加"
```

---

### Task 5: `forgot-password.html`とログイン画面からのリンク

**Files:**
- Create: `admin-ui/forgot-password.html`
- Modify: `admin-ui/tenant-login.html`
- Modify: `nginx/conf.d/api.conf`(`/admin/`ロケーションのCSP)
- Modify: `docs/install-guide.html`(reCAPTCHAキー置換手順に`forgot-password.html`を追記)

**Interfaces:**
- Consumes: `POST /tenant-auth/forgot-password`(Task 3)

- [ ] **Step 1: `admin-ui/forgot-password.html`を新規作成**

`public-ui/signup.html`と同じ構成を踏襲する: `<script src="https://www.google.com/recaptcha/api.js?render=__RECAPTCHA_SITE_KEY__">`、フォーム項目は「テナントスラッグ・メールアドレス」の2つ、送信時に`grecaptcha.execute('__RECAPTCHA_SITE_KEY__', { action: 'forgot_password' })`でトークンを取得し`POST /tenant-auth/forgot-password`へ送信。送信後は常に「該当するアカウントがあれば数分以内にメールが届きます。届かない場合はメールアドレスが間違っている可能性があります」という文言を表示する(エラー時も含め、入力バリデーションエラー以外はこの文言に統一する)。`admin-ui/tenant-login.html`への戻りリンクを添える。

- [ ] **Step 2: `admin-ui/tenant-login.html`に「パスワードを忘れた方はこちら」リンクを追加**

既存の「試用登録はこちらで」リンク(`<p class="text-center text-sm text-gray-500 mt-4">`配下)の直後に、同じスタイルで`<a href="/admin/forgot-password.html">パスワードを忘れた方はこちら</a>`を追加する。

- [ ] **Step 3: `nginx/conf.d/api.conf`の`/admin/`ロケーションにCSPを追加**

既存の`/signup/`ロケーションに追加したCSP緩和(`script-src`/`frame-src`/`connect-src`に`https://www.google.com/recaptcha/`・`https://www.gstatic.com/recaptcha/`を追加)と同じ内容を、`/admin/`ロケーションにも追加する。`/admin/`は既存ページがAlpine.js(`'unsafe-eval'`が必要)を使うため、`script-src`は`'self' 'unsafe-inline' 'unsafe-eval' https://www.google.com/recaptcha/ https://www.gstatic.com/recaptcha/`とする。他の基本ヘッダー(`X-Content-Type-Options`等)も`/signup/`と同様に再指定する。

- [ ] **Step 4: `docs/install-guide.html`のテナントセルフサインアップ手順に注記を追加**

既存の手順3(`__RECAPTCHA_SITE_KEY__`のsed置換)の対象ファイルに`admin-ui/forgot-password.html`も追加する一文を加える。

- [ ] **Step 5: HTML構文確認**

Run: 既存の`html.parser`によるタグ対応チェックスクリプトを`admin-ui/forgot-password.html`・`admin-ui/tenant-login.html`に対して実行する。
Expected: 両ファイルともタグの対応に問題なし

- [ ] **Step 6: Commit**

```bash
git add admin-ui/forgot-password.html admin-ui/tenant-login.html nginx/conf.d/api.conf docs/install-guide.html
git commit -m "feat(password-reset): パスワード再設定リクエストページとCSP緩和を追加"
```

---

### Task 6: `reset-password.html`

**Files:**
- Create: `admin-ui/reset-password.html`

**Interfaces:**
- Consumes: `POST /tenant-auth/reset-password`(Task 4)

- [ ] **Step 1: `admin-ui/reset-password.html`を新規作成**

`public-ui/verify.html`と同じ構成を踏襲する: URLの`?token=`をJavaScriptで読み取り、新しいパスワード入力欄(確認用含む、2つの値が一致することをクライアント側でも検証)を表示。送信すると`POST /tenant-auth/reset-password`に`{token, new_password}`を送る。成功(`204`)時は「パスワードを更新しました」と表示し、`admin-ui/tenant-login.html`へのリンクを出す。失敗時(`404`/`410`)は`body.detail`、無ければ「リンクが無効か期限切れです。もう一度パスワード再設定をお試しください」と表示し、`admin-ui/forgot-password.html`へのリンクを出す。

- [ ] **Step 2: HTML構文確認**

Run: 既存の`html.parser`によるタグ対応チェックスクリプトを`admin-ui/reset-password.html`に対して実行する。
Expected: タグの対応に問題なし

- [ ] **Step 3: Commit**

```bash
git add admin-ui/reset-password.html
git commit -m "feat(password-reset): パスワード再設定画面を追加"
```

## 完了条件

- Task 1〜4: `cd core-api && python -m pytest -q`が全件PASS
- Task 5〜6: HTML構文チェック(html.parserでタグ対応確認)でエラーが無いこと
