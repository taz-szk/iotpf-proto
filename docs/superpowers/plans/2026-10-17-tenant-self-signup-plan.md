# テナントセルフサインアップ機能 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** プラットフォーム管理者への連絡なしに、見込み利用者が自分でテナントを作成して90日間トライアル利用できる機能を実装する。

**Architecture:** 新規公開ルーター`signup.py`がreCAPTCHA v3検証とメール確認トークンによる仮登録を管理し、確認完了時に既存の`setup_tenant()`(InfluxDB org/Grafana org/スキーマ作成)を再利用して本物のテナントを作る。トライアル期限は`core-api`内のPythonスクリプト(cronから`docker compose exec`で起動)が日次でチェックし、警告メール送信と自動削除を行う。延長申請はテナントポータルAPIから既存のSlack Webhookへ直接通知する。

**Tech Stack:** FastAPI / SQLAlchemy(生SQL中心) / pytest / Alpine.js(静的HTML) / nginx / Google reCAPTCHA v3

**Spec:** `docs/superpowers/specs/2026-10-17-tenant-self-signup-design.md`

## Global Constraints

- 既存の`validate_password`(12文字以上・72バイト以下、emailと同一不可)をそのまま適用する
- 既存のテナント分離(スキーマ分離・mTLS・EMQX ACL)は変更しない
- トライアル延長は何度でも可、却下操作は作らない(無視すれば次サイクルで削除)
- Slack通知は既存の`post_slack_message()`(`app/services/slack_webhook.py`)を再利用し、新規Webhookは作らない
- `signup_source="self_signup"`のテナントは月次請求バッチ(`run_monthly_billing_batch`)の対象から除外する
- `public-ui/`はプラットフォーム管理者専用の`platform-ui/`とは別の、完全公開の新規静的ディレクトリ

## Review Focus

1. **確認メール送信失敗**: サインアップ自体を`500`で失敗とし、仮登録行をロールバックする(ユーザーが「送信した気になっているが実際は失敗」という状態を作らない)
2. **確認リンクの二重クリック・期限切れ後クリック**: 1回目でテナント作成・行削除、2回目は`404`(テナントが二重に作られない)。期限切れは`410`
3. **同一slug希望の確認時競合**: `Tenant.slug`のunique制約違反時に仮登録行を残したままエラーを返し、ユーザーがリトライできるようにする
4. **reCAPTCHAスコアが閾値未満**: `success: true`だが低スコアのトークンを拒否できること
5. **suspended中のトライアルテナントを自動削除しない**: `trial_expiry_check.py`の削除条件が`status == "active"`限定であること

---

### Task 1: データモデル・設定追加

**Files:**
- Modify: `core-api/app/models/public.py`(`Tenant`クラスにカラム追加、新規`TenantSignupRequest`クラス追加)
- Modify: `core-api/app/database.py`(移行関数2つ追加)
- Modify: `core-api/app/main.py`(移行関数の登録)
- Modify: `core-api/app/config.py`(設定追加)
- Modify: `core-api/.env.example`(新規設定の雛形追加)
- Test: `core-api/tests/test_signup_models.py`

**Interfaces:**
- Produces: `Tenant.signup_source: str`, `Tenant.trial_expires_at: datetime | None`, `Tenant.trial_warning_sent_at: datetime | None`, `Tenant.trial_extension_requested_at: datetime | None`
- Produces: `TenantSignupRequest`(`id`, `email`, `tenant_name`, `slug`, `password_hash`, `verification_token`, `created_at`, `expires_at`)
- Produces: `settings.recaptcha_site_key: str`, `settings.recaptcha_secret_key: str`, `settings.recaptcha_min_score: float`, `settings.vuln_scan_slack_webhook_url: str`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_signup_models.py
from app.models.public import Tenant, TenantSignupRequest


def test_tenant_has_trial_columns():
    cols = {c.name for c in Tenant.__table__.columns}
    assert {"signup_source", "trial_expires_at", "trial_warning_sent_at",
            "trial_extension_requested_at"} <= cols


def test_tenant_signup_request_table_columns():
    cols = {c.name for c in TenantSignupRequest.__table__.columns}
    assert cols == {"id", "email", "tenant_name", "slug", "password_hash",
                     "verification_token", "created_at", "expires_at"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_signup_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'TenantSignupRequest'`

- [ ] **Step 3: Implement**

`core-api/app/models/public.py`の`Tenant`クラス(既存の`data_retention_days`行の直後)に追加:

```python
    signup_source = Column(String(20), nullable=False, default="platform_created")
    trial_expires_at = Column(DateTime(timezone=True), nullable=True)
    trial_warning_sent_at = Column(DateTime(timezone=True), nullable=True)
    trial_extension_requested_at = Column(DateTime(timezone=True), nullable=True)
```

同ファイルの`PlatformUser`クラスの後に追加:

```python
class TenantSignupRequest(Base):
    __tablename__ = "tenant_signup_requests"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), nullable=False)
    tenant_name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True)
    password_hash = Column(String, nullable=False)
    verification_token = Column(String(64), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
```

`core-api/app/database.py`の末尾に追加(既存の`migrate_*`関数と同じ形):

```python
def migrate_add_trial_columns() -> None:
    """tenants テーブルにセルフサインアップ・トライアル管理用カラムを追加する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE tenants
            ADD COLUMN IF NOT EXISTS signup_source VARCHAR(20) NOT NULL DEFAULT 'platform_created',
            ADD COLUMN IF NOT EXISTS trial_expires_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS trial_warning_sent_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS trial_extension_requested_at TIMESTAMPTZ
        """))
        conn.commit()


def migrate_create_tenant_signup_requests() -> None:
    """tenant_signup_requests テーブル(メール確認待ちの仮登録)を作成する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tenant_signup_requests (
                id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email            VARCHAR(255) NOT NULL,
                tenant_name      VARCHAR(255) NOT NULL,
                slug             VARCHAR(100) NOT NULL UNIQUE,
                password_hash    TEXT NOT NULL,
                verification_token VARCHAR(64) NOT NULL UNIQUE,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at       TIMESTAMPTZ NOT NULL
            )
        """))
        conn.commit()
```

`core-api/app/main.py`の6行目のimportに`migrate_add_trial_columns, migrate_create_tenant_signup_requests`を追加し、30行目の`for migrate in (...)`のタプル末尾にも同じ2つを追加する。

`core-api/app/config.py`の`smtp_from`行の直後に追加:

```python
    recaptcha_site_key: str = ""
    recaptcha_secret_key: str = ""
    recaptcha_min_score: float = 0.5
    # vuln-scan.sh / secrets-rotation-check.sh と同じSlack Webhookをプラットフォーム通知に再利用する
    vuln_scan_slack_webhook_url: str = ""
```

`core-api/.env.example`の末尾に追加:

```
RECAPTCHA_SITE_KEY=changeme_recaptcha_site_key
RECAPTCHA_SECRET_KEY=changeme_recaptcha_secret_key
RECAPTCHA_MIN_SCORE=0.5
```

(`VULN_SCAN_SLACK_WEBHOOK_URL`は既存のトップレベル`.env.example`に既にあるため追記不要。core-apiがこの変数を読むようになったことだけ`.env.example`該当行にコメントで補足する。)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_signup_models.py -v`
Expected: PASS (2/2)

- [ ] **Step 5: Run full suite**

Run: `cd core-api && python -m pytest -q`
Expected: 既存テストも含め全件PASS(モデル追加のみで既存ロジックに影響しないため)

- [ ] **Step 6: Commit**

```bash
git add core-api/app/models/public.py core-api/app/database.py core-api/app/main.py core-api/app/config.py core-api/.env.example core-api/tests/test_signup_models.py
git commit -m "feat(signup): トライアル管理用カラムとtenant_signup_requestsテーブルを追加"
```

---

### Task 2: slugify ユーティリティ

**Files:**
- Create: `core-api/app/services/slug.py`
- Test: `core-api/tests/test_slug.py`

**Interfaces:**
- Produces: `slugify(name: str, is_taken: Callable[[str], bool]) -> str`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_slug.py
from app.services.slug import slugify


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Acme  Corp!!", is_taken=lambda s: False) == "acme-corp"


def test_slugify_strips_non_alnum_and_leading_trailing_hyphens():
    assert slugify("  -- 日本語Test株式会社 --  ", is_taken=lambda s: False) == "test"


def test_slugify_empty_after_cleanup_falls_back_to_tenant():
    assert slugify("日本語のみ", is_taken=lambda s: False) == "tenant"


def test_slugify_appends_suffix_on_collision():
    taken = {"acme-corp", "acme-corp-2"}
    assert slugify("Acme Corp", is_taken=lambda s: s in taken) == "acme-corp-3"


def test_slugify_no_collision_no_suffix():
    assert slugify("Acme Corp", is_taken=lambda s: False) == "acme-corp"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_slug.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.slug'`

- [ ] **Step 3: Implement**

```python
# core-api/app/services/slug.py
import re
from typing import Callable

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(name: str, is_taken: Callable[[str], bool]) -> str:
    """表示名からURL/DB向けのslugを作る。英数字以外は捨てるため、全て非ASCII文字の
    名前は空になりうる — その場合は"tenant"にフォールバックする。
    is_takenでTrueが返るたびに連番を付けて再試行する。"""
    base = _NON_ALNUM.sub("-", name.lower()).strip("-")
    if not base:
        base = "tenant"

    candidate = base
    suffix = 1
    while is_taken(candidate):
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_slug.py -v`
Expected: PASS (5/5)

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/slug.py core-api/tests/test_slug.py
git commit -m "feat(signup): tenant_nameからslugを自動生成するslugifyユーティリティを追加"
```

---

### Task 3: テナント初期ユーザー作成ロジックの共通化

既存の`routers/tenant_users.py`の`create_tenant_user`が持つDB挿入+Grafana同期ロジックを、
サインアップ確認フローからも呼べるように`services/tenant_users.py`へ切り出す。既存エンドポイントの
挙動は変えない(同じロジックを呼ぶだけ)。

**Files:**
- Create: `core-api/app/services/tenant_users.py`
- Modify: `core-api/app/routers/tenant_users.py:1-64`
- Test: `core-api/tests/test_tenant_users_service.py`
- Test(既存、回帰確認のみ): `core-api/tests/test_tenant_users_api.py`(ファイル名は実際のテストファイル名に合わせる。無ければこのステップで該当する既存テストファイル名をリポジトリから確認する)

**Interfaces:**
- Consumes: なし(Task 1までの変更と独立)
- Produces: `create_tenant_user_record(tenant_id: str, schema: str, email: str, password_hash: str, role: str, grafana_org_id: str | None) -> None`(既存の`IntegrityError`はそのまま上に伝播させる — 呼び出し側でHTTPエラーに変換する)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenant_users_service.py
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy.exc import IntegrityError
from app.services.tenant_users import create_tenant_user_record


def test_create_tenant_user_record_inserts_and_syncs_grafana():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.tenant_users.engine") as mock_engine, \
         patch("app.services.tenant_users.ensure_grafana_user_in_org") as mock_grafana:
        mock_engine.connect.return_value = mock_conn
        create_tenant_user_record(
            tenant_id="t1", schema="tenant_t1", email="a@example.com",
            password_hash="hashed", role="admin", grafana_org_id="5",
        )
    assert mock_conn.execute.called
    mock_grafana.assert_called_once_with(5, "a@example.com", "Viewer", "t1")
    mock_conn.commit.assert_called_once()


def test_create_tenant_user_record_skips_grafana_when_no_org():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.tenant_users.engine") as mock_engine, \
         patch("app.services.tenant_users.ensure_grafana_user_in_org") as mock_grafana:
        mock_engine.connect.return_value = mock_conn
        create_tenant_user_record(
            tenant_id="t1", schema="tenant_t1", email="a@example.com",
            password_hash="hashed", role="viewer", grafana_org_id=None,
        )
    mock_grafana.assert_not_called()


def test_create_tenant_user_record_propagates_integrity_error():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.execute.side_effect = IntegrityError("dup", {}, Exception("dup"))
    with patch("app.services.tenant_users.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        with pytest.raises(IntegrityError):
            create_tenant_user_record(
                tenant_id="t1", schema="tenant_t1", email="a@example.com",
                password_hash="hashed", role="admin", grafana_org_id=None,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenant_users_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.tenant_users'`

- [ ] **Step 3: Implement**

```python
# core-api/app/services/tenant_users.py
from sqlalchemy import text
from app.database import engine
from app.services.grafana import ensure_grafana_user_in_org

_ROLE_GRAFANA = {"admin": "Viewer", "operator": "Viewer", "viewer": "Viewer"}


def create_tenant_user_record(
    tenant_id: str, schema: str, email: str, password_hash: str,
    role: str, grafana_org_id: str | None,
) -> None:
    """テナントスキーマのusersテーブルへ1行挿入し、Grafana orgにも同期する。
    email重複はIntegrityErrorとして呼び出し側に伝播する(HTTPエラーへの変換は呼び出し側の責務)。"""
    with engine.connect() as conn:
        conn.execute(
            text(f'''
                INSERT INTO "{schema}".users (email, password_hash, role)
                VALUES (:email, :hash, :role)
            '''),
            {"email": email, "hash": password_hash, "role": role},
        )
        if grafana_org_id:
            ensure_grafana_user_in_org(int(grafana_org_id), email, _ROLE_GRAFANA[role], tenant_id)
        conn.commit()
```

`core-api/app/routers/tenant_users.py`の`create_tenant_user`本体(46-64行目の`with engine.connect() as conn:`ブロック)を、新しい共通関数呼び出しに置き換える:

```python
    try:
        create_tenant_user_record(
            tenant_id=tenant_id_str, schema=schema, email=body.email,
            password_hash=password_hash, role=body.role,
            grafana_org_id=tenant.grafana_org_id,
        )
    except IntegrityError:
        raise HTTPException(status_code=409, detail="Email already exists")
```

(この変更に伴い、ファイル先頭のimportから`engine`, `ensure_grafana_user_in_org`, `_ROLE_GRAFANA`の直接使用が不要になった場合は削除し、`from app.services.tenant_users import create_tenant_user_record`を追加する。`uuid`/`user_id`変数は元々DBのDEFAULT gen_random_uuid()を使わず明示付番していた点に注意 — `id`列をINSERT文から外すと`gen_random_uuid()`または`uuid_generate_v4()`のDEFAULTに委ねる形になるため、`database.py`の`users`テーブル定義(`DEFAULT uuid_generate_v4()`)がある前提を確認し、挙動が変わらないことをStep 5で既存テストで確認する。)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenant_users_service.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Run existing tenant_users router tests to confirm no regression**

Run: `cd core-api && python -m pytest tests/ -k tenant_users -v`
Expected: 既存テストも全てPASS(リファクタのみで外部挙動は変わらないため)

- [ ] **Step 6: Commit**

```bash
git add core-api/app/services/tenant_users.py core-api/app/routers/tenant_users.py core-api/tests/test_tenant_users_service.py
git commit -m "refactor(signup): テナント初期ユーザー作成ロジックをservices/tenant_users.pyへ切り出し"
```

---

### Task 4: メール送信関数の追加

**Files:**
- Modify: `core-api/app/services/mailer.py`
- Test: `core-api/tests/test_mailer_signup.py`

**Interfaces:**
- Produces: `send_signup_verification_email(to_email: str, tenant_name: str, verification_url: str) -> None`(送信失敗時は例外を伝播させる — 既存`send_bill_shock_email`と異なり、サインアップはユーザーに即時結果を返す必要があるため)
- Produces: `send_trial_expiry_warning_email(to_email: str, tenant_name: str, expires_at_date: str) -> None`(送信失敗時は例外を伝播させず、呼び出し元の`trial_expiry_check.py`のバッチを止めない。既存`send_bill_shock_email`と同じ方針)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_mailer_signup.py
from unittest.mock import patch, MagicMock
import pytest
from app.services.mailer import send_signup_verification_email, send_trial_expiry_warning_email


def test_send_signup_verification_email_sends_with_link():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = instance
        send_signup_verification_email(
            "user@example.com", "Acme Corp", "https://example.local/signup/verify?token=abc",
        )
    assert instance.sendmail.called
    args = instance.sendmail.call_args[0]
    assert "https://example.local/signup/verify?token=abc" in args[2]


def test_send_signup_verification_email_raises_on_failure():
    with patch("app.services.mailer.smtplib.SMTP", side_effect=ConnectionError("boom")):
        with pytest.raises(ConnectionError):
            send_signup_verification_email("user@example.com", "Acme Corp", "https://x/verify?token=t")


def test_send_trial_expiry_warning_email_sends():
    with patch("app.services.mailer.smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = instance
        send_trial_expiry_warning_email("admin@example.com", "Acme Corp", "2026-12-01")
    assert instance.sendmail.called
    args = instance.sendmail.call_args[0]
    assert "2026-12-01" in args[2]


def test_send_trial_expiry_warning_email_swallows_failure():
    with patch("app.services.mailer.smtplib.SMTP", side_effect=ConnectionError("boom")):
        send_trial_expiry_warning_email("admin@example.com", "Acme Corp", "2026-12-01")
    # 例外が伝播しなければテストは成功で終わる
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_mailer_signup.py -v`
Expected: FAIL with `ImportError: cannot import name 'send_signup_verification_email'`

- [ ] **Step 3: Implement**

`core-api/app/services/mailer.py`の末尾に追加:

```python
def send_signup_verification_email(to_email: str, tenant_name: str, verification_url: str) -> None:
    """サインアップの確認メールを送信する。送信失敗は呼び出し元(POST /signup)に伝播させ、
    ユーザーに失敗として返す(送信できていないのに成功扱いにしない)。"""
    tenant_name_clean = str(tenant_name).replace("\r", "").replace("\n", "")
    msg = MIMEMultipart()
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg["Subject"] = f"[IoT Platform] {tenant_name_clean} のサインアップ確認"
    msg.attach(MIMEText(
        f"以下のリンクをクリックしてサインアップを完了してください(24時間以内)。\n\n{verification_url}\n",
        "plain",
    ))
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
        if settings.smtp_user:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_from, [to_email], msg.as_string())


def send_trial_expiry_warning_email(to_email: str, tenant_name: str, expires_at_date: str) -> None:
    """トライアル期限7日前の警告メールを送信する。送信失敗はログ出力のみ
    (既存のsend_bill_shock_emailと同じ方針。バッチ全体を止めない)。"""
    tenant_name_clean = str(tenant_name).replace("\r", "").replace("\n", "")
    msg = MIMEMultipart()
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg["Subject"] = f"[IoT Platform] {tenant_name_clean} のトライアル期限が近づいています"
    msg.attach(MIMEText(
        f"テナント「{tenant_name_clean}」のトライアル利用期限は {expires_at_date} です。\n"
        "継続利用を希望する場合は、管理画面から延長を申請してください。\n",
        "plain",
    ))
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_user:
                server.starttls()
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from, [to_email], msg.as_string())
    except Exception as e:
        print(f"Trial expiry warning email send failed: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_mailer_signup.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/mailer.py core-api/tests/test_mailer_signup.py
git commit -m "feat(signup): サインアップ確認メール・トライアル期限警告メールの送信関数を追加"
```

---

### Task 5: reCAPTCHA v3 検証ヘルパー

**Files:**
- Create: `core-api/app/services/recaptcha.py`
- Test: `core-api/tests/test_recaptcha.py`

**Interfaces:**
- Produces: `verify_recaptcha(token: str, remote_ip: str | None = None) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_recaptcha.py
from unittest.mock import patch, MagicMock
from app.services.recaptcha import verify_recaptcha


def test_verify_recaptcha_accepts_high_score():
    resp = MagicMock()
    resp.json.return_value = {"success": True, "score": 0.9}
    with patch("app.services.recaptcha.httpx.post", return_value=resp):
        assert verify_recaptcha("good-token") is True


def test_verify_recaptcha_rejects_low_score():
    resp = MagicMock()
    resp.json.return_value = {"success": True, "score": 0.1}
    with patch("app.services.recaptcha.httpx.post", return_value=resp):
        assert verify_recaptcha("bot-token") is False


def test_verify_recaptcha_rejects_when_success_false():
    resp = MagicMock()
    resp.json.return_value = {"success": False, "error-codes": ["invalid-input-response"]}
    with patch("app.services.recaptcha.httpx.post", return_value=resp):
        assert verify_recaptcha("bad-token") is False


def test_verify_recaptcha_rejects_on_network_error():
    with patch("app.services.recaptcha.httpx.post", side_effect=Exception("timeout")):
        assert verify_recaptcha("any-token") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_recaptcha.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.recaptcha'`

- [ ] **Step 3: Implement**

```python
# core-api/app/services/recaptcha.py
import httpx
from app.config import settings

_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def verify_recaptcha(token: str, remote_ip: str | None = None) -> bool:
    """reCAPTCHA v3のトークンを検証する。successがTrueかつscoreが
    settings.recaptcha_min_score以上の場合のみTrue。通信失敗時もFalse
    (サインアップは拒否する側に倒す)。"""
    data = {"secret": settings.recaptcha_secret_key, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        resp = httpx.post(_VERIFY_URL, data=data, timeout=10)
        body = resp.json()
    except Exception:
        return False
    return bool(body.get("success")) and body.get("score", 0) >= settings.recaptcha_min_score
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_recaptcha.py -v`
Expected: PASS (4/4)

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/recaptcha.py core-api/tests/test_recaptcha.py
git commit -m "feat(signup): reCAPTCHA v3検証ヘルパーを追加"
```

---

### Task 6: サインアップ・メール確認ルーター

**Files:**
- Create: `core-api/app/routers/signup.py`
- Modify: `core-api/app/main.py`(ルーター登録)
- Test: `core-api/tests/test_signup_api.py`

**Interfaces:**
- Consumes: `slugify`(Task 2)、`create_tenant_user_record`(Task 3)、`send_signup_verification_email`(Task 4)、`verify_recaptcha`(Task 5)、既存`setup_tenant`(`app/services/tenant.py`)、既存`validate_password`、既存`is_rate_limited`/`record_failure`
- Produces: `POST /signup`(`201`)、`GET /signup/verify?token=...`(`200`/`404`/`410`/`409`)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_signup_api.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from app.main import app
from app.services.rate_limiter import clear_failures

client = TestClient(app)


def _session_ctx(query_results=None):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    if query_results is not None:
        mock_db.query.return_value.filter.return_value.first.return_value = query_results
    return mock_db


def _signup_body(email="user@example.com"):
    return {
        "tenant_name": "Acme Corp",
        "email": email,
        "password": "correct-horse-battery-staple",
        "recaptcha_token": "good-token",
    }


def test_signup_rejects_low_recaptcha_score():
    clear_failures("signup:testclient")
    with patch("app.routers.signup.verify_recaptcha", return_value=False):
        resp = client.post("/signup", json=_signup_body())
    assert resp.status_code == 400


def test_signup_creates_pending_request_and_sends_email():
    clear_failures("signup:testclient")
    with patch("app.routers.signup.verify_recaptcha", return_value=True), \
         patch("app.routers.signup.SessionLocal") as mock_session, \
         patch("app.routers.signup.send_signup_verification_email") as mock_mail:
        db = _session_ctx(query_results=None)  # 既存の未確認リクエスト・Tenant.slugどちらも無し
        mock_session.return_value = db
        resp = client.post("/signup", json=_signup_body())
    assert resp.status_code == 201
    assert db.add.called
    assert mock_mail.called


def test_signup_fails_when_email_send_fails_and_rolls_back():
    clear_failures("signup:testclient")
    with patch("app.routers.signup.verify_recaptcha", return_value=True), \
         patch("app.routers.signup.SessionLocal") as mock_session, \
         patch("app.routers.signup.send_signup_verification_email", side_effect=ConnectionError("smtp down")):
        db = _session_ctx(query_results=None)
        mock_session.return_value = db
        resp = client.post("/signup", json=_signup_body())
    assert resp.status_code == 500
    assert db.rollback.called


def test_verify_rejects_unknown_token():
    with patch("app.routers.signup.SessionLocal") as mock_session:
        db = _session_ctx(query_results=None)
        mock_session.return_value = db
        resp = client.get("/signup/verify", params={"token": "does-not-exist"})
    assert resp.status_code == 404


def test_verify_rejects_expired_token():
    expired_request = MagicMock(
        id="r1", email="user@example.com", tenant_name="Acme Corp", slug="acme-corp",
        password_hash="hashed",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    with patch("app.routers.signup.SessionLocal") as mock_session:
        db = _session_ctx(query_results=expired_request)
        mock_session.return_value = db
        resp = client.get("/signup/verify", params={"token": "expired-token"})
    assert resp.status_code == 410
    assert db.delete.called


def test_verify_returns_409_on_slug_conflict():
    valid_request = MagicMock(
        id="r1", email="user@example.com", tenant_name="Acme Corp", slug="acme-corp",
        password_hash="hashed",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with patch("app.routers.signup.SessionLocal") as mock_session:
        db = _session_ctx(query_results=valid_request)
        db.flush.side_effect = IntegrityError("dup", {}, Exception("dup"))
        mock_session.return_value = db
        resp = client.get("/signup/verify", params={"token": "valid-token"})
    assert resp.status_code == 409
    assert db.rollback.called
    db.delete.assert_not_called()  # 仮登録行は残し、ユーザーがリトライできるようにする


def test_verify_creates_tenant_and_deletes_signup_request():
    valid_request = MagicMock(
        id="r1", email="user@example.com", tenant_name="Acme Corp", slug="acme-corp",
        password_hash="hashed",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    with patch("app.routers.signup.SessionLocal") as mock_session, \
         patch("app.routers.signup.setup_tenant", return_value=("org1", "tok1", "5")), \
         patch("app.routers.signup.create_tenant_user_record") as mock_create_user, \
         patch("app.routers.signup.write_audit_log"):
        db = _session_ctx(query_results=valid_request)
        mock_session.return_value = db
        resp = client.get("/signup/verify", params={"token": "valid-token"})
    assert resp.status_code == 200
    assert db.add.called  # 新しいTenant行
    assert mock_create_user.called
    assert db.delete.called  # tenant_signup_requests行の削除
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_signup_api.py -v`
Expected: FAIL (404、signup.pyが存在せずルーター未登録)

- [ ] **Step 3: Implement**

```python
# core-api/app/routers/signup.py
import uuid
from datetime import datetime, timedelta, timezone

import secrets
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import SessionLocal
from app.models.public import Tenant, TenantSignupRequest
from app.services.audit import write_audit_log
from app.services.auth import hash_password
from app.services.mailer import send_signup_verification_email
from app.services.password_policy import validate_password
from app.services.rate_limiter import is_rate_limited, record_failure
from app.services.recaptcha import verify_recaptcha
from app.services.slug import slugify
from app.services.tenant import setup_tenant
from app.services.tenant_users import create_tenant_user_record

router = APIRouter()

_REQUEST_TTL = timedelta(hours=24)
_TRIAL_PERIOD = timedelta(days=90)


class SignupRequest(BaseModel):
    tenant_name: str
    email: EmailStr
    password: str
    recaptcha_token: str


@router.post("/signup", status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    rate_key = f"signup:{ip}"
    if is_rate_limited(rate_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many requests")

    if not verify_recaptcha(body.recaptcha_token, ip):
        record_failure(rate_key)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reCAPTCHA verification failed")

    validate_password(body.password, body.email)

    with SessionLocal() as db:
        existing_pending = db.query(TenantSignupRequest).filter(
            TenantSignupRequest.email == body.email
        ).first()
        if existing_pending:
            db.delete(existing_pending)
            db.flush()

        def _slug_taken(candidate: str) -> bool:
            return (
                db.query(Tenant).filter(Tenant.slug == candidate).first() is not None
                or db.query(TenantSignupRequest).filter(TenantSignupRequest.slug == candidate).first() is not None
            )

        slug = slugify(body.tenant_name, _slug_taken)
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        signup_req = TenantSignupRequest(
            id=uuid.uuid4(), email=body.email, tenant_name=body.tenant_name, slug=slug,
            password_hash=hash_password(body.password), verification_token=token,
            expires_at=now + _REQUEST_TTL,
        )
        db.add(signup_req)
        db.flush()

        verification_url = f"https://{settings.platform_domain}/signup/verify?token={token}"
        try:
            send_signup_verification_email(body.email, body.tenant_name, verification_url)
        except Exception:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail="Failed to send verification email")

        db.commit()
    return {"status": "verification_email_sent"}


@router.get("/signup/verify")
def verify_signup(token: str):
    with SessionLocal() as db:
        signup_req = db.query(TenantSignupRequest).filter(
            TenantSignupRequest.verification_token == token
        ).first()
        if not signup_req:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invalid or already used token")

        expires_at = signup_req.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            db.delete(signup_req)
            db.commit()
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="Verification link expired")

        tenant_id = uuid.uuid4()
        tenant = Tenant(
            id=tenant_id, name=signup_req.tenant_name, slug=signup_req.slug,
            signup_source="self_signup",
            trial_expires_at=datetime.now(timezone.utc) + _TRIAL_PERIOD,
        )
        db.add(tenant)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="Slug already taken, please try signing up again")

        tenant_id_str = str(tenant_id)
        org_id, influx_token, grafana_org_id = setup_tenant(tenant_id_str, signup_req.tenant_name)
        tenant.influxdb_org_id = org_id
        tenant.influxdb_token = influx_token
        tenant.grafana_org_id = str(grafana_org_id)

        schema = f"tenant_{tenant_id_str.replace('-', '_')}"
        create_tenant_user_record(
            tenant_id=tenant_id_str, schema=schema, email=signup_req.email,
            password_hash=signup_req.password_hash, role="admin", grafana_org_id=str(grafana_org_id),
        )

        write_audit_log(db, "self_signup", tenant_id_str, signup_req.email,
                        "signup_tenant", tenant_id=tenant_id_str, resource_type="tenant",
                        resource_id=signup_req.tenant_name)

        db.delete(signup_req)
        db.commit()

    return {"status": "tenant_created", "tenant_id": tenant_id_str}
```

`core-api/app/main.py`の`from app.routers import ...`に`signup`を追加し、`app.include_router(signup.router)`を他のルーター登録と並べて追加する。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_signup_api.py -v`
Expected: PASS (8/8)

- [ ] **Step 5: Run full suite**

Run: `cd core-api && python -m pytest -q`
Expected: 全件PASS

- [ ] **Step 6: Commit**

```bash
git add core-api/app/routers/signup.py core-api/app/main.py core-api/tests/test_signup_api.py
git commit -m "feat(signup): POST /signup と GET /signup/verify を追加"
```

---

### Task 7: 月次請求バッチからトライアルテナントを除外

**Files:**
- Modify: `core-api/app/services/billing_batch.py:220`
- Test: `core-api/tests/test_billing_batch.py`(既存ファイルに追記)

**Interfaces:**
- Consumes: `Tenant.signup_source`(Task 1)

- [ ] **Step 1: Write the failing test**

既存`core-api/tests/test_billing_batch.py`の既存テストの直近に追記(既存のテストダブルの作り方に合わせて調整すること。以下は最小の意図を示す):

```python
def test_run_monthly_billing_batch_excludes_self_signup_tenants():
    from unittest.mock import patch, MagicMock
    from app.services.billing_batch import run_monthly_billing_batch

    active_tenant = MagicMock(id="t1", status="active", signup_source="platform_created")
    trial_tenant = MagicMock(id="t2", status="active", signup_source="self_signup")

    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.all.return_value = [active_tenant]

    with patch("app.services.billing_batch.SessionLocal") as mock_session:
        mock_session.return_value = mock_db
        run_monthly_billing_batch()

    filter_call = mock_db.query.return_value.filter.call_args
    assert filter_call is not None
```

(このテストは「`signup_source != 'self_signup'`の条件がクエリに含まれること」を、モックされた
`filter`呼び出しの実引数を確認する形で検証する。既存テストファイルの`run_monthly_billing_batch`の
呼び方・モックの粒度に合わせて、実装時にアサーションを調整すること。)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_billing_batch.py -k self_signup -v`
Expected: FAIL(現状のクエリには`signup_source`条件が無いため、期待する`filter`引数が無い)

- [ ] **Step 3: Implement**

`core-api/app/services/billing_batch.py:220`付近の

```python
tenants = db.query(Tenant).filter(Tenant.status == "active").all()
```

を

```python
tenants = db.query(Tenant).filter(
    Tenant.status == "active", Tenant.signup_source != "self_signup"
).all()
```

に変更する。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_billing_batch.py -v`
Expected: 全件PASS

- [ ] **Step 5: Commit**

```bash
git add core-api/app/services/billing_batch.py core-api/tests/test_billing_batch.py
git commit -m "fix(signup): 月次請求バッチからセルフサインアップのトライアルテナントを除外"
```

---

### Task 8: 延長申請エンドポイント(テナントポータル)

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`(末尾付近に追加)
- Test: `core-api/tests/test_tenant_portal_trial_extension.py`

**Interfaces:**
- Consumes: `post_slack_message`(既存`app/services/slack_webhook.py`)、`slack_escape`(同)、`settings.vuln_scan_slack_webhook_url`(Task 1)
- Produces: `POST /tenant-portal/me/trial-extension-request`(`200`/`400`)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenant_portal_trial_extension.py
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _admin_token():
    return create_access_token({
        "sub": "user-id", "email": "admin@tenant.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": "admin",
    })


def _session_ctx(tenant):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = tenant
    return mock_db


def test_trial_extension_request_rejects_non_self_signup_tenant():
    tenant = MagicMock(id=TENANT_ID, name="Acme", signup_source="platform_created")
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx(tenant)
        resp = client.post(
            "/tenant-portal/me/trial-extension-request",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
    assert resp.status_code == 400


def test_trial_extension_request_notifies_slack_and_sets_timestamp():
    tenant = MagicMock(id=TENANT_ID, name="Acme", signup_source="self_signup",
                       trial_extension_requested_at=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.post_slack_message") as mock_slack, \
         patch("app.routers.tenant_portal.settings") as mock_settings:
        mock_settings.vuln_scan_slack_webhook_url = "https://hooks.slack.com/services/T/B/X"
        mock_session.return_value = _session_ctx(tenant)
        resp = client.post(
            "/tenant-portal/me/trial-extension-request",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
    assert resp.status_code == 200
    assert tenant.trial_extension_requested_at is not None
    assert mock_slack.called


def test_trial_extension_request_skips_slack_when_webhook_not_configured():
    tenant = MagicMock(id=TENANT_ID, name="Acme", signup_source="self_signup",
                       trial_extension_requested_at=None)
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.post_slack_message") as mock_slack, \
         patch("app.routers.tenant_portal.settings") as mock_settings:
        mock_settings.vuln_scan_slack_webhook_url = ""
        mock_session.return_value = _session_ctx(tenant)
        resp = client.post(
            "/tenant-portal/me/trial-extension-request",
            headers={"Authorization": f"Bearer {_admin_token()}"},
        )
    assert resp.status_code == 200
    mock_slack.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_trial_extension.py -v`
Expected: FAIL with `404`(エンドポイント未実装)

- [ ] **Step 3: Implement**

`core-api/app/routers/tenant_portal.py`の末尾に追加(ファイル先頭のimportに
`from datetime import datetime, timezone`(未importなら追加)、
`from app.services.slack_webhook import post_slack_message, slack_escape`、`from app.config import settings`を追加):

```python
@router.post("/me/trial-extension-request")
def request_trial_extension(payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        if tenant.signup_source != "self_signup":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Trial extension is only for self-signup tenants")
        if tenant.trial_extension_requested_at is None:
            tenant.trial_extension_requested_at = datetime.now(timezone.utc)
            db.commit()
            if settings.vuln_scan_slack_webhook_url:
                text = (
                    f"🔔 トライアル延長申請\n"
                    f"テナント: {slack_escape(tenant.name)}\n"
                    f"tenant_id: {tenant_id}"
                )
                post_slack_message(settings.vuln_scan_slack_webhook_url, text)
    return {"status": "requested"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_trial_extension.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Run full suite**

Run: `cd core-api && python -m pytest -q`
Expected: 全件PASS

- [ ] **Step 6: Commit**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_tenant_portal_trial_extension.py
git commit -m "feat(signup): テナント側のトライアル延長申請エンドポイントを追加"
```

---

### Task 9: 承認エンドポイント(プラットフォーム管理者)とTenantOut拡張

**Files:**
- Modify: `core-api/app/routers/tenants.py`(末尾に追加)
- Modify: `core-api/app/schemas/tenant.py`
- Test: `core-api/tests/test_tenants_trial_extension_approve.py`

**Interfaces:**
- Produces: `POST /tenants/{tenant_id}/trial-extension/approve`(`200`/`404`)
- Produces: `TenantOut.signup_source: str`, `TenantOut.trial_expires_at: datetime | None`, `TenantOut.trial_extension_requested_at: datetime | None`

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_tenants_trial_extension_approve.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _session_ctx(tenant):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = tenant
    return mock_db


def test_approve_extends_trial_and_clears_flags():
    old_expiry = datetime.now(timezone.utc) + timedelta(days=1)
    tenant = MagicMock(
        id=TENANT_ID, name="Acme", trial_expires_at=old_expiry,
        trial_extension_requested_at=datetime.now(timezone.utc),
        trial_warning_sent_at=datetime.now(timezone.utc),
    )
    with patch("app.routers.tenants.SessionLocal") as mock_session, \
         patch("app.routers.tenants.write_audit_log"):
        mock_session.return_value = _session_ctx(tenant)
        resp = client.post(
            f"/tenants/{TENANT_ID}/trial-extension/approve",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert tenant.trial_expires_at > old_expiry
    assert tenant.trial_extension_requested_at is None
    assert tenant.trial_warning_sent_at is None


def test_approve_requires_platform_auth():
    resp = client.post(f"/tenants/{TENANT_ID}/trial-extension/approve")
    assert resp.status_code == 401


def test_approve_404_when_tenant_missing():
    with patch("app.routers.tenants.SessionLocal") as mock_session:
        mock_session.return_value = _session_ctx(None)
        resp = client.post(
            f"/tenants/{TENANT_ID}/trial-extension/approve",
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_tenants_trial_extension_approve.py -v`
Expected: FAIL with `404`(エンドポイント未実装)

- [ ] **Step 3: Implement**

`core-api/app/schemas/tenant.py`の`TenantOut`に追加:

```python
    signup_source: str = "platform_created"
    trial_expires_at: Optional[datetime] = None
    trial_extension_requested_at: Optional[datetime] = None
```

`core-api/app/routers/tenants.py`の末尾に追加(ファイル先頭に`from datetime import datetime, timedelta, timezone`が無ければ追加。既存の`import time`はそのまま残す):

```python
_TRIAL_PERIOD = timedelta(days=90)


@router.post("/{tenant_id}/trial-extension/approve", response_model=TenantOut)
def approve_trial_extension(tenant_id: str, payload: dict = Depends(_require_platform)):
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
        tenant.trial_expires_at = datetime.now(timezone.utc) + _TRIAL_PERIOD
        tenant.trial_extension_requested_at = None
        tenant.trial_warning_sent_at = None
        write_audit_log(db, "platform", payload["sub"], payload["email"],
                        "approve_trial_extension", tenant_id=tenant_id, resource_type="tenant",
                        resource_id=tenant.name)
        db.commit()
        db.refresh(tenant)
        return tenant
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_tenants_trial_extension_approve.py -v`
Expected: PASS (3/3)

- [ ] **Step 5: Run full suite**

Run: `cd core-api && python -m pytest -q`
Expected: 全件PASS

- [ ] **Step 6: Commit**

```bash
git add core-api/app/routers/tenants.py core-api/app/schemas/tenant.py core-api/tests/test_tenants_trial_extension_approve.py
git commit -m "feat(signup): プラットフォーム管理者向けのトライアル延長承認エンドポイントを追加"
```

---

### Task 10: トライアル期限チェック・自動削除スクリプト

**Files:**
- Create: `core-api/app/scripts/__init__.py`(空ファイル)
- Create: `core-api/app/scripts/trial_expiry_check.py`
- Test: `core-api/tests/test_trial_expiry_check.py`

**Interfaces:**
- Consumes: `send_trial_expiry_warning_email`(Task 4)、既存`teardown_tenant`(`app/services/tenant.py`)
- Produces: `run_trial_expiry_check() -> dict`(`{"warned": int, "deleted": int}`。`python -m app.scripts.trial_expiry_check`実行時にこれを呼ぶ`if __name__ == "__main__":`ブロックを持つ)

- [ ] **Step 1: Write the failing test**

```python
# core-api/tests/test_trial_expiry_check.py
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from app.scripts.trial_expiry_check import run_trial_expiry_check


def _tenant(id_, status_, trial_expires_at, warning_sent=None, ext_requested=None):
    return MagicMock(
        id=id_, name=f"tenant-{id_}", status=status_, signup_source="self_signup",
        trial_expires_at=trial_expires_at, trial_warning_sent_at=warning_sent,
        trial_extension_requested_at=ext_requested,
        influxdb_org_id="org", grafana_org_id="5",
    )


def _session_ctx(tenants):
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.all.return_value = tenants
    return mock_db


def test_sends_warning_for_tenant_expiring_within_7_days():
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "active", now + timedelta(days=5))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.send_trial_expiry_warning_email") as mock_mail, \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        result = run_trial_expiry_check()
    assert mock_mail.called
    assert tenant.trial_warning_sent_at is not None
    mock_teardown.assert_not_called()
    assert result == {"warned": 1, "deleted": 0}


def test_does_not_rewarn_already_warned_tenant():
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "active", now + timedelta(days=5), warning_sent=now - timedelta(days=1))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.send_trial_expiry_warning_email") as mock_mail, \
         patch("app.scripts.trial_expiry_check.teardown_tenant"):
        mock_session.return_value = _session_ctx([tenant])
        run_trial_expiry_check()
    mock_mail.assert_not_called()


def test_deletes_expired_tenant_without_extension_request():
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "active", now - timedelta(days=1))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.send_trial_expiry_warning_email"), \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        result = run_trial_expiry_check()
    assert tenant.status == "deleted"
    mock_teardown.assert_called_once_with("t1", "org", "5")
    assert result == {"warned": 0, "deleted": 1}


def test_does_not_delete_when_extension_requested():
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "active", now - timedelta(days=1), ext_requested=now - timedelta(days=1))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        run_trial_expiry_check()
    mock_teardown.assert_not_called()
    assert tenant.status == "active"


def test_does_not_delete_suspended_tenant():
    # status=="active"限定のクエリ条件自体が守るべき不変条件。ここではクエリが
    # 返したテナント一覧自体にsuspendedが含まれないことを前提に、仮にactive以外が
    # 混入しても削除対象のフィルタ条件(status確認)で弾かれることを確認する。
    now = datetime.now(timezone.utc)
    tenant = _tenant("t1", "suspended", now - timedelta(days=1))
    with patch("app.scripts.trial_expiry_check.SessionLocal") as mock_session, \
         patch("app.scripts.trial_expiry_check.teardown_tenant") as mock_teardown:
        mock_session.return_value = _session_ctx([tenant])
        run_trial_expiry_check()
    mock_teardown.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd core-api && python -m pytest tests/test_trial_expiry_check.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.scripts'`

- [ ] **Step 3: Implement**

```python
# core-api/app/scripts/trial_expiry_check.py
"""トライアル(セルフサインアップ)テナントの期限を日次でチェックする。
cronから `docker compose exec -T core-api python -m app.scripts.trial_expiry_check` で起動する。"""
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.public import Tenant
from app.services.mailer import send_trial_expiry_warning_email
from app.services.tenant import teardown_tenant

_WARNING_WINDOW = timedelta(days=7)


def run_trial_expiry_check() -> dict:
    now = datetime.now(timezone.utc)
    warned = 0
    deleted = 0

    with SessionLocal() as db:
        tenants = db.query(Tenant).filter(
            Tenant.signup_source == "self_signup", Tenant.status == "active",
        ).all()

        for tenant in tenants:
            expires_at = tenant.trial_expires_at
            if expires_at is None:
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at <= now and tenant.trial_extension_requested_at is None:
                tenant.status = "deleted"
                tenant.slug = f"{tenant.slug}_del{int(now.timestamp())}"
                influxdb_org_id = tenant.influxdb_org_id
                grafana_org_id = tenant.grafana_org_id
                tenant_id = str(tenant.id)
                db.commit()
                teardown_tenant(tenant_id, influxdb_org_id, grafana_org_id)
                deleted += 1
                continue

            if (expires_at - now) <= _WARNING_WINDOW and tenant.trial_warning_sent_at is None:
                with SessionLocal() as admin_db:
                    schema = f"tenant_{str(tenant.id).replace('-', '_')}"
                    from sqlalchemy import text
                    admin_email = admin_db.execute(
                        text(f'SELECT email FROM "{schema}".users WHERE role = \'admin\' ORDER BY created_at LIMIT 1')
                    ).scalar()
                if admin_email:
                    send_trial_expiry_warning_email(
                        admin_email, tenant.name, expires_at.date().isoformat(),
                    )
                tenant.trial_warning_sent_at = now
                db.commit()
                warned += 1

    return {"warned": warned, "deleted": deleted}


if __name__ == "__main__":
    result = run_trial_expiry_check()
    print(f"trial_expiry_check: warned={result['warned']} deleted={result['deleted']}")
```

(テストでは`admin_db.execute`を伴う管理者email取得部分までは踏み込んでモックしていないため、
Step 1のテストで`send_trial_expiry_warning_email`が呼ばれることまでは検証できるが、
`admin_db`周りは実装時に`SessionLocal`の2回目呼び出しとして正しくモックされるかをStep 4で確認し、
必要ならテストの`_session_ctx`に`execute().scalar()`の戻り値も設定するよう調整すること。)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd core-api && python -m pytest tests/test_trial_expiry_check.py -v`
Expected: PASS (5/5)。`SessionLocal`が2回(外側のテナント一覧取得用と、内側の管理者email取得用)呼ばれる構造になっているため、テストのモックが`mock_session.return_value`を1つしか返さない場合は2回目の呼び出しでも同じモックが返り、`execute().scalar()`の戻り値(デフォルトでは`MagicMock`)が`None`判定にならず`send_trial_expiry_warning_email`に渡ってしまう可能性がある。実行結果を見て、`mock_db.execute.return_value.scalar.return_value = "admin@example.com"`等をテストに追加すること。

- [ ] **Step 5: Commit**

```bash
git add core-api/app/scripts/__init__.py core-api/app/scripts/trial_expiry_check.py core-api/tests/test_trial_expiry_check.py
git commit -m "feat(signup): トライアル期限チェック・警告メール・自動削除スクリプトを追加"
```

---

### Task 11: nginx・docker-compose配線(public-ui公開・レート制限)

このタスクはpytestの対象外(インフラ設定)。`docker compose config`での構文確認と、
ローカルでDocker Desktopが使える場合のみ実機確認を行う。

**Files:**
- Create: `public-ui/`ディレクトリ(Task 12で中身を作成。このタスクでは空の`.gitkeep`を置くだけで良い)
- Modify: `nginx/conf.d/rate-limit.conf`
- Modify: `nginx/conf.d/api.conf`
- Modify: `docker-compose.yml`(nginxサービスのvolumes)

**Interfaces:**
- Consumes: なし
- Produces: `https://<platform_domain>/signup/`(静的ページ)、レート制限された`/api/signup`

- [ ] **Step 1: 変更前の設定を確認**

```bash
cat nginx/conf.d/rate-limit.conf
```

Expected: `limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;`の1行のみ

- [ ] **Step 2: レート制限ゾーンを追加**

`nginx/conf.d/rate-limit.conf`に追記:

```nginx
limit_req_zone $binary_remote_addr zone=signup:10m rate=10r/m;
```

- [ ] **Step 3: `/api/signup`専用のレート制限ブロックを追加**

`nginx/conf.d/api.conf`の既存`location = /api/tenant-auth/login { ... }`ブロックの直後に追加:

```nginx
    location = /api/signup {
        limit_req zone=signup burst=5 nodelay;
        set $core_api http://core-api:8000;
        rewrite ^/api(/.*) $1 break;
        proxy_pass $core_api;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
    }
```

(`GET /api/signup/verify`はパスが異なるため、この`location =`完全一致ブロックには
マッチせず、既存の一般`location /api/ { ... }`経由でcore-apiに届く。追加のレート制限は不要
— トークンはランダムで推測できないため)

- [ ] **Step 4: 公開サインアップページのnginx location追加**

`nginx/conf.d/api.conf`の`location /iotairx-console/ { ... }`ブロックの直後に追加:

```nginx
    # 公開セルフサインアップページ。認証不要。
    location /signup/ {
        alias /usr/share/nginx/public-ui/;
        index signup.html;
        try_files $uri $uri/ /signup/signup.html;
    }
```

- [ ] **Step 5: docker-compose.ymlのnginxサービスにvolumeマウント追加**

`docker-compose.yml`のnginxサービスの既存`- ./admin-ui:/usr/share/nginx/admin-ui:ro`の行の直後に追加:

```yaml
      - ./public-ui:/usr/share/nginx/public-ui:ro
```

- [ ] **Step 6: Create placeholder directory**

```bash
mkdir -p public-ui
touch public-ui/.gitkeep
```

(Task 12で`signup.html`等を追加した時点で`.gitkeep`は不要になるため削除する)

- [ ] **Step 7: 構文確認**

```bash
docker compose config --quiet
```

Expected: エラーなし終了(コマンドが何も出力せず終了コード0)。Docker Desktopが使えない環境では
`docker compose config`自体が繋がらずエラーになるため、その場合はYAML構文のみ
`python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"`で確認する。

- [ ] **Step 8: Commit**

```bash
git add nginx/conf.d/rate-limit.conf nginx/conf.d/api.conf docker-compose.yml public-ui/.gitkeep
git commit -m "feat(signup): 公開サインアップページのnginx配線とレート制限ゾーンを追加"
```

---

### Task 12: 公開サインアップページ(静的HTML)

**Files:**
- Create: `public-ui/signup.html`
- Create: `public-ui/signup-pending.html`
- Delete: `public-ui/.gitkeep`(Task 11で作成した placeholder)

**Interfaces:**
- Consumes: `POST /api/signup`(Task 6)

- [ ] **Step 1: signup.htmlを作成**

既存`docs/promo.html`のトーン(配色・フォント)を参考に、以下の内容で作成する:

```html
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>無料トライアル申込み - IoTAirx</title>
  <script src="https://www.google.com/recaptcha/api.js?render=__RECAPTCHA_SITE_KEY__"></script>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 480px; margin: 60px auto; padding: 0 20px; color: #1f2937; }
    h1 { font-size: 1.5rem; margin-bottom: 1.5rem; }
    label { display: block; margin-top: 1rem; font-size: 0.875rem; color: #4b5563; }
    input { width: 100%; padding: 0.5rem; margin-top: 0.25rem; border: 1px solid #d1d5db; border-radius: 0.375rem; box-sizing: border-box; }
    button { margin-top: 1.5rem; width: 100%; padding: 0.75rem; background: #2563eb; color: white; border: none; border-radius: 0.375rem; font-size: 1rem; cursor: pointer; }
    button:disabled { background: #93c5fd; cursor: wait; }
    #error { color: #dc2626; margin-top: 1rem; font-size: 0.875rem; }
  </style>
</head>
<body>
  <h1>無料トライアル申込み（90日間）</h1>
  <form id="signup-form">
    <label>テナント名(会社名等)<input type="text" id="tenant_name" required maxlength="255"></label>
    <label>メールアドレス<input type="email" id="email" required></label>
    <label>パスワード(12文字以上)<input type="password" id="password" required minlength="12"></label>
    <button type="submit" id="submit-btn">申込む</button>
    <div id="error"></div>
  </form>
  <script>
    document.getElementById('signup-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const btn = document.getElementById('submit-btn');
      const errorEl = document.getElementById('error');
      errorEl.textContent = '';
      btn.disabled = true;
      try {
        const token = await grecaptcha.execute('__RECAPTCHA_SITE_KEY__', { action: 'signup' });
        const resp = await fetch('/api/signup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            tenant_name: document.getElementById('tenant_name').value,
            email: document.getElementById('email').value,
            password: document.getElementById('password').value,
            recaptcha_token: token,
          }),
        });
        if (!resp.ok) {
          const body = await resp.json().catch(() => ({}));
          throw new Error(body.detail || '申込みに失敗しました');
        }
        window.location.href = '/signup/signup-pending.html';
      } catch (err) {
        errorEl.textContent = err.message;
        btn.disabled = false;
      }
    });
  </script>
</body>
</html>
```

`__RECAPTCHA_SITE_KEY__`は実際のデプロイ時に`settings.recaptcha_site_key`相当の値へ
置き換える必要がある(現状このプロジェクトのadmin-ui/platform-uiは静的HTML配信のみでテンプレート
展開の仕組みが無いため、このタスクの範囲では placeholder のまま残し、デプロイ手順書
(`docs/install-guide.html`等)に「`public-ui/signup.html`内の`__RECAPTCHA_SITE_KEY__`を
実際のサイトキーに置換すること」という手順を追記することを次のステップで行う)。

- [ ] **Step 2: signup-pending.htmlを作成**

```html
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>確認メール送信済み - IoTAirx</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 480px; margin: 100px auto; padding: 0 20px; color: #1f2937; text-align: center; }
  </style>
</head>
<body>
  <h1>確認メールを送信しました</h1>
  <p>メール内のリンクをクリックして、サインアップを完了してください（24時間以内）。</p>
</body>
</html>
```

- [ ] **Step 3: placeholderの削除**

```bash
git rm public-ui/.gitkeep
```

- [ ] **Step 4: デプロイ手順への追記**

`docs/install-guide.html`(既存のインストールガイド)に、reCAPTCHAキー発行と
`RECAPTCHA_SITE_KEY`/`RECAPTCHA_SECRET_KEY`の設定、および`public-ui/signup.html`内の
`__RECAPTCHA_SITE_KEY__`置換が必要であることを追記する(既存ドキュメントの該当セクション
— AIアシスタントの初期セットアップ節等、他のオプション機能セットアップ手順と同じ並びに追加する)。

- [ ] **Step 5: 目視確認**

ブラウザで`public-ui/signup.html`を直接開き(`file://`)、フォームの表示が崩れていないことを
確認する(reCAPTCHAスクリプトはキー未設定のため動かないが、レイアウト確認は可能)。

- [ ] **Step 6: Commit**

```bash
git add public-ui/signup.html public-ui/signup-pending.html docs/install-guide.html
git commit -m "feat(signup): 公開サインアップページの静的HTMLを追加"
```

---

### Task 13: プラットフォーム管理者向けテナント一覧UIの拡張

**Files:**
- Modify: `platform-ui/tenants.html`

**Interfaces:**
- Consumes: `TenantOut.signup_source`/`trial_expires_at`/`trial_extension_requested_at`(Task 9)、`POST /tenants/{tenant_id}/trial-extension/approve`(Task 9)

- [ ] **Step 1: テーブルヘッダーに列を追加**

`platform-ui/tenants.html`の既存`<th>`(作成日の列、100-103行目付近)の直後に追加:

```html
            <th class="text-left px-6 py-3 text-xs font-medium text-gray-500 uppercase">トライアル期限</th>
```

- [ ] **Step 2: 行データに期限・延長申請中バッジ・承認ボタンを追加**

既存の作成日`<td>`(123-124行目)の直後に追加:

```html
              <td class="px-6 py-4 text-sm">
                <template x-if="tenant.signup_source === 'self_signup'">
                  <div>
                    <span x-text="tenant.trial_expires_at ? new Date(tenant.trial_expires_at).toLocaleDateString('ja-JP') : '-'"
                          class="text-gray-600"></span>
                    <template x-if="tenant.trial_extension_requested_at">
                      <div class="mt-1">
                        <span class="inline-block px-2 py-0.5 bg-yellow-100 text-yellow-700 text-xs rounded">延長申請中</span>
                        <button @click="approveExtension(tenant)"
                                :disabled="tenant._approving"
                                class="ml-1 px-2 py-0.5 text-xs font-medium text-blue-600 border border-blue-300 rounded hover:bg-blue-50 disabled:opacity-50">
                          <span x-text="tenant._approving ? '処理中...' : '承認'"></span>
                        </button>
                      </div>
                    </template>
                  </div>
                </template>
                <template x-if="tenant.signup_source !== 'self_signup'">
                  <span class="text-gray-400">-</span>
                </template>
              </td>
```

- [ ] **Step 3: `tenantsApp()`に`approveExtension`メソッドを追加**

既存の`openDeleteModal(tenant) { ... }`メソッドの直後に追加(既存の`api.js`ヘルパーが
どう認証ヘッダーを付けているかを`/admin/js/api.js`で確認し、既存の`toggleStatus`等の
fetch呼び出しと同じ方式(関数名・呼び出し方)に合わせて実装すること。以下は意図を示す最小形):

```javascript
        async approveExtension(tenant) {
          tenant._approving = true;
          try {
            const resp = await apiFetch(`/api/tenants/${tenant.id}/trial-extension/approve`, { method: 'POST' });
            if (!resp.ok) throw new Error('承認に失敗しました');
            const updated = await resp.json();
            tenant.trial_expires_at = updated.trial_expires_at;
            tenant.trial_extension_requested_at = updated.trial_extension_requested_at;
          } catch (e) {
            this.error = e.message;
          } finally {
            tenant._approving = false;
          }
        },
```

(`apiFetch`は`/admin/js/api.js`が実際に公開している関数名に合わせること。異なる名前・
異なる認証方式であれば、既存の`toggleStatus`メソッドの実装をそのまま参考にする。)

- [ ] **Step 2: 目視確認**

ローカルでDocker Composeが使える場合は`docker compose up -d`後に
`https://localhost/iotairx-console/tenants.html`をブラウザで開き、既存テナント一覧が
崩れずに表示されることを確認する(トライアル列は`signup_source`が無い既存テナントでは
「-」表示になるはず)。使えない場合はHTMLの構文(閉じタグの対応)のみ目視で確認する。

- [ ] **Step 3: Commit**

```bash
git add platform-ui/tenants.html
git commit -m "feat(signup): テナント一覧にトライアル期限・延長承認UIを追加"
```

---

## 完了条件

- Task 1〜10: `cd core-api && python -m pytest -q`が全件PASS
- Task 11: `docker compose config --quiet`がエラーなく終了(または環境制約によりYAML構文チェックで代替)
- Task 12, 13: 目視確認(ブラウザ or 静的ファイルを直接開く)でレイアウト崩れがないこと
