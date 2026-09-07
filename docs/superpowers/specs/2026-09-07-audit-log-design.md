# 監査ログ機能 実装仕様

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** テナント単位・PF管理者単位で「誰がいつ何に対してどのような操作をしたか」を記録し、管理者が画面で確認できる監査ログ機能を追加する。

**Architecture:** PostgreSQL の `public.audit_logs` テーブルに全ユーザー操作を記録する。記録はルーターの各エンドポイントで `write_audit_log()` を明示的に呼ぶ方式（ミドルウェア自動収集ではなく意図した操作のみを記録）。閲覧はPF管理者が全テナントを、テナント管理者が自テナント分のみ参照できる。

**Tech Stack:** FastAPI / SQLAlchemy / PostgreSQL / Alpine.js / Tailwind CSS（既存スタック踏襲）

**Spec:** `docs/superpowers/specs/2026-09-07-audit-log-design.md`（本ファイル）

## Global Constraints

- Python 3.11+、FastAPI、SQLAlchemy 2.x（既存バージョン踏襲）
- マイグレーションは `database.py` に `migrate_create_audit_logs()` を追加し、`main.py` の `on_startup()` で呼ぶ（べき等、`CREATE TABLE IF NOT EXISTS`）
- 既存の認証パターン（`_require_platform` / `_require_tenant_admin`）を踏襲
- フロントエンドは Alpine.js + Tailwind CSS（`npm run build:css` を実装後に実行すること）
- `write_audit_log()` の呼び出しは既存のDBセッションと同一トランザクション内で行う（ログだけ別commitしない）

---

## DBスキーマ

### `public.audit_logs` テーブル

```sql
CREATE TABLE IF NOT EXISTS audit_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_type    VARCHAR(20)  NOT NULL,             -- 'platform' | 'tenant'
    actor_id      UUID         NOT NULL,
    actor_email   VARCHAR(255) NOT NULL,
    tenant_id     UUID         REFERENCES tenants(id) ON DELETE SET NULL,  -- NULL = PF操作
    action        VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50),
    resource_id   VARCHAR(255),
    detail        JSONB,
    ip_address    VARCHAR(45),
    result        VARCHAR(10)  NOT NULL DEFAULT 'success',  -- 'success' | 'failure'
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_audit_logs_tenant_created
    ON audit_logs(tenant_id, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_audit_logs_created
    ON audit_logs(created_at DESC);
```

### SQLAlchemy モデル（`app/models/public.py` に追加）

```python
from sqlalchemy.dialects.postgresql import UUID, JSONB

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_type    = Column(String(20),  nullable=False)
    actor_id      = Column(UUID(as_uuid=True), nullable=False)
    actor_email   = Column(String(255), nullable=False)
    tenant_id     = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    action        = Column(String(100), nullable=False)
    resource_type = Column(String(50),  nullable=True)
    resource_id   = Column(String(255), nullable=True)
    detail        = Column(JSONB,       nullable=True)
    ip_address    = Column(String(45),  nullable=True)
    result        = Column(String(10),  nullable=False, default="success")
    created_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
```

---

## 記録対象アクション一覧

| カテゴリ | action 値 | resource_type | resource_id の内容 |
|---|---|---|---|
| 認証（PF） | `login_success` | `platform_user` | email |
| 認証（PF） | `login_failure` | `platform_user` | email |
| 認証（PF） | `logout` | `platform_user` | email |
| 認証（PF） | `change_password` | `platform_user` | email |
| 認証（テナント） | `login_success` | `tenant_user` | email |
| 認証（テナント） | `login_failure` | `tenant_user` | email |
| 認証（テナント） | `logout` | `tenant_user` | email |
| 認証（テナント） | `change_password` | `tenant_user` | email |
| TOTP | `totp_enabled` | `platform_user` / `tenant_user` | email |
| TOTP | `totp_disabled` | `platform_user` / `tenant_user` | email |
| MFA設定 | `mfa_settings_updated` | `mfa_settings` | `"platform"` / `"tenant"` |
| テナント管理 | `create_tenant` | `tenant` | テナント名 |
| テナント管理 | `update_tenant` | `tenant` | テナント名 |
| テナント管理 | `delete_tenant` | `tenant` | テナント名 |
| テナント管理 | `suspend_tenant` | `tenant` | テナント名 |
| テナント管理 | `activate_tenant` | `tenant` | テナント名 |
| テナントユーザー | `create_tenant_user` | `tenant_user` | email |
| テナントユーザー | `delete_tenant_user` | `tenant_user` | email |
| デバイス | `create_device` | `device` | device_id |
| デバイス | `delete_device` | `device` | device_id |
| ファームウェア | `upload_firmware` | `firmware` | バージョン文字列 |
| ファームウェア | `delete_firmware` | `firmware` | バージョン文字列 |
| OTA | `ota_send` | `device` | device_id（detail にファームウェアバージョン） |
| プロビジョニングトークン | `create_provisioning_token` | `provisioning_token` | トークンID |
| プロビジョニングトークン | `delete_provisioning_token` | `provisioning_token` | トークンID |

---

## ヘルパーサービス（`app/services/audit.py` 新規）

```python
from sqlalchemy.orm import Session
from app.models.public import AuditLog
import uuid as _uuid

def write_audit_log(
    db: Session,
    actor_type: str,        # "platform" | "tenant"
    actor_id: str,
    actor_email: str,
    action: str,
    tenant_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
    result: str = "success",
) -> None:
    """監査ログを現在のDBセッションに追加する。commitは呼び出し側が行う。"""
    db.add(AuditLog(
        id=_uuid.uuid4(),
        actor_type=actor_type,
        actor_id=_uuid.UUID(actor_id),
        actor_email=actor_email,
        tenant_id=_uuid.UUID(tenant_id) if tenant_id else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail,
        ip_address=ip_address,
        result=result,
    ))
```

---

## 保存期間と定期削除

### `app/config.py` に追加

```python
audit_log_retention_days: int = Field(default=365)
# 環境変数 AUDIT_LOG_RETENTION_DAYS で上書き可
```

### 定期削除ジョブ（`app/services/audit.py` に追加）

```python
import threading
import time
from datetime import datetime, timedelta, timezone
from app.database import SessionLocal
from app.config import settings

def _purge_old_audit_logs() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.audit_log_retention_days)
    with SessionLocal() as db:
        db.query(AuditLog).filter(AuditLog.created_at < cutoff).delete()
        db.commit()

def start_audit_purge_worker() -> None:
    """毎日1回古い監査ログを削除するバックグラウンドスレッドを起動する。"""
    def _loop():
        while True:
            try:
                _purge_old_audit_logs()
            except Exception:
                pass
            time.sleep(86400)  # 24時間
    threading.Thread(target=_loop, daemon=True).start()
```

`main.py` の `on_startup()` で `start_audit_purge_worker()` を呼ぶ。

---

## APIエンドポイント

### PF管理者向け（`app/routers/audit_logs.py` 新規）

```
GET /audit-logs
  Query params:
    tenant_id: str (optional) — テナントIDで絞り込み
    action:    str (optional) — アクション種別で絞り込み
    from_dt:   str (optional) — 開始日時 ISO8601 (e.g. 2026-09-01T00:00:00Z)
    to_dt:     str (optional) — 終了日時 ISO8601
    limit:     int (default=50, max=100)
    offset:    int (default=0)

Response:
  {
    "total": int,
    "items": [AuditLogOut]
  }
```

認証: `_require_platform` (既存パターン)

### テナント管理者向け（`app/routers/tenant_portal.py` に追加）

```
GET /tenant-portal/audit-logs
  Query params:
    action:  str (optional)
    from_dt: str (optional)
    to_dt:   str (optional)
    limit:   int (default=50, max=100)
    offset:  int (default=0)

Response: 同上（tenant_id は JWT から自動取得、他テナント参照不可）
```

認証: `_require_tenant_admin` (既存パターン)

### レスポンススキーマ（`AuditLogOut`）

```python
class AuditLogOut(BaseModel):
    id:            str
    actor_type:    str
    actor_email:   str
    tenant_id:     str | None
    action:        str
    resource_type: str | None
    resource_id:   str | None
    detail:        dict | None
    ip_address:    str | None
    result:        str
    created_at:    datetime

    model_config = ConfigDict(from_attributes=True)
```

---

## UI

### PF管理者画面（`admin-ui/audit-logs.html` 新規）

- 既存の管理者ナビゲーション（`platform.html` 等）に「監査ログ」リンクを追加
- フィルター行: テナント選択ドロップダウン / アクション選択ドロップダウン / 開始日〜終了日 / 検索ボタン
- テーブル列: 日時（ローカルTZ） / 操作者メール / テナント名 / アクション / 対象リソース / 結果バッジ / IP
- 結果バッジ: `success` = 緑、`failure` = 赤
- ページネーション: 50件/ページ、前/次ボタン
- Alpine.js で動的フィルタリング・ページ切替（既存パターンと統一）

### テナントポータル（`tenant-portal.html` に新タブ追加）

- 既存タブ群の末尾に「監査ログ」タブを追加
- フィルター: アクション選択 / 開始日〜終了日（テナント絞り込み不要）
- テーブル列: 日時 / 操作者メール / アクション / 対象リソース / 結果バッジ / IP
- ページネーション: 50件/ページ

---

## マイグレーション

`database.py` に追加:

```python
def migrate_create_audit_logs() -> None:
    """audit_logs テーブルを作成する（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                actor_type    VARCHAR(20)  NOT NULL,
                actor_id      UUID         NOT NULL,
                actor_email   VARCHAR(255) NOT NULL,
                tenant_id     UUID         REFERENCES tenants(id) ON DELETE SET NULL,
                action        VARCHAR(100) NOT NULL,
                resource_type VARCHAR(50),
                resource_id   VARCHAR(255),
                detail        JSONB,
                ip_address    VARCHAR(45),
                result        VARCHAR(10)  NOT NULL DEFAULT 'success',
                created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_audit_logs_tenant_created
                ON audit_logs(tenant_id, created_at DESC)
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_audit_logs_created
                ON audit_logs(created_at DESC)
        """))
        conn.commit()
```

`main.py` の `on_startup()` に `migrate_create_audit_logs` を追加。

---

## スコープ外（本仕様に含まない）

- APIアクセスログ（GET含む全HTTPリクエスト記録）— 別途要件定義
- 監査ログのエクスポート機能（CSV等）
- ログの改ざん防止（ハッシュチェーン等）
- 保存期間のテナント別設定（全テナント共通の設定値を使用）
