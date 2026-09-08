# デバイスグループ管理機能（バックエンドAPI）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** テナント配下のデバイスをグループにまとめ、アラートルール・OTA配信・ダッシュボードパネル設定をグループ単位で扱えるようにするバックエンドAPI（データモデル・グループCRUD・デバイス割当・アラートルール対象化・OTA一括配信・ダッシュボード設定上書き）を実装する。

**Architecture:** 既存の「テナントスキーマ内テーブル + プラットフォーム管理者ルーター(`/tenants/{tenant_id}/...`) + テナントポータルルーター(`/tenant-portal/me/...`) の二面展開」パターンをそのまま踏襲する。グループCRUDのDB操作は `app/services/device_groups.py` に共通実装し、2つのルーターから呼び出すことで重複を避ける（既存の alert_rules.py / tenant_portal.py のように SQL を完全に複製している箇所とは異なる新パターンだが、グループCRUDは5箇所で使われるロジックであるため共通化する）。

**Tech Stack:** FastAPI, SQLAlchemy Core（生SQL中心）, PostgreSQL（テナントごとの動的スキーマ）, pytest + unittest.mock（実DB不要のモックテスト）

**Spec:** `docs/superpowers/specs/2026-09-08-device-groups-design.md`

**このプランでの仕様書からの逸脱（要確認）:**
- テナントポータル側のエンドポイントを、仕様書記載の `/tenant-portal/groups` ではなく `/tenant-portal/me/groups` にする。既存の `tenant_portal.py` はデバイス・ユーザー・アラートルール・ファームウェアなど「テナント自身が所有するリソースのCRUD」を一貫して `/me/...` 配下に置いており（`/dashboard` や `/audit-logs` は例外）、グループもこの分類に該当するため既存規約を優先した。
- ダッシュボードパネル設定のグループ別上書きは Grafana ダッシュボード同期（`sync_tenant_dashboard_with_configs`）の対象外とする。仕様書に明記がなく、既存の同期機構がグループ非対応のため、テナント全体のデフォルト設定（`group_id IS NULL`）のみ同期対象とする。

## Global Constraints

- テナントスキーマ名は `tenant_{tenant_id.replace('-', '_')}`（ハイフン→アンダースコア）。
- 生SQLは `sqlalchemy.text()` を使用し、テーブル名/スキーマ名はf-stringで埋め込み・値は必ずbindparamで渡す（既存コードの一貫した方針）。
- tenant_id / UUID形式の検証は既存の `re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', v.lower())` パターンを流用する。
- 監査ログは `app.services.audit.write_audit_log(db, ...)`（同一トランザクション内）または `log_audit(...)`（独立セッション、例外を握りつぶす）を使う。DB更新と同一コミットに含めたい場合は `write_audit_log`、更新後の別処理として記録するだけなら `log_audit`。
- マイグレーション関数は `core-api/app/database.py` に追加し、`core-api/app/main.py` の `on_startup()` 内のタプルに登録する（全て冪等・失敗しても他のmigrationを止めない設計）。**CREATE TABLE IF NOT EXISTS は非互換な既存テーブルがあると無言でスキップされる問題が過去にあった**（監査ログ機能で発生）。`device_groups` は完全新規テーブルなのでこのリスクはないが、`ALTER TABLE ... ADD CONSTRAINT`（IF NOT EXISTS非対応）は `DO $$ ... END $$` で存在チェックしてから実行すること。
- テストは全て `unittest.mock.patch` で `SessionLocal` / `engine` をモックし、実DBを使わない（このリポジトリのテスト方針）。実行は `cd core-api && python -m pytest tests/ -v`。

---

## File Structure

- Modify: `core-api/app/database.py` — `device_groups` テーブル・`group_id` 列を `create_tenant_schema()` に追加、既存テナント向け `migrate_device_groups()` と `migrate_dashboard_panel_config_group_id()` を追加
- Modify: `core-api/app/main.py` — 新規マイグレーション関数の登録、新規ルーターの登録
- Modify: `core-api/app/models/public.py` — `DashboardPanelConfig` に `group_id` 列追加、UNIQUE制約変更
- Create: `core-api/app/schemas/device_group.py` — `GroupCreate` / `GroupUpdate` / `GroupOut`
- Create: `core-api/app/services/device_groups.py` — グループCRUD・デバイス割当・グループ内デバイス一覧の共通DB操作、専用例外クラス
- Create: `core-api/app/routers/device_groups.py` — プラットフォーム管理者向けグループCRUD (`/tenants/{tenant_id}/groups`)
- Modify: `core-api/app/routers/tenant_devices.py` — デバイスのグループ割当PATCH追加、`DeviceOut` に `group_id` 追加
- Modify: `core-api/app/routers/alert_rules.py` — `group_id` 対応・排他バリデーション
- Modify: `core-api/app/routers/firmware.py` — グループ一括OTA送信エンドポイント追加
- Modify: `core-api/app/routers/tenant_portal.py` — グループCRUD・デバイス割当PATCH・アラートルールgroup_id対応・グループ一括OTA・ダッシュボードパネル設定group_id対応
- Create: `core-api/tests/test_device_groups.py` — サービス層ユニットテスト
- Create: `core-api/tests/test_device_groups_api.py` — プラットフォーム/テナントポータル グループCRUD APIテスト
- Create: `core-api/tests/test_device_group_assignment.py` — デバイス割当APIテスト
- Create: `core-api/tests/test_alert_rules_group.py` — アラートルールのgroup_id・排他バリデーションテスト
- Create: `core-api/tests/test_device_groups_ota.py` — グループ一括OTAテスト
- Modify: `core-api/tests/test_db.py` — マイグレーション関数のテスト追加
- Modify: `core-api/tests/test_dashboard_panel_config.py` — group_idクエリパラメータのテスト追加

---

### Task 1: データモデル・マイグレーション

**Files:**
- Modify: `core-api/app/database.py`
- Modify: `core-api/app/main.py`
- Modify: `core-api/app/models/public.py`
- Test: `core-api/tests/test_db.py`

**Interfaces:**
- Produces: `create_tenant_schema(tenant_id)` が `"{schema}".device_groups` テーブルと `devices.group_id` / `alert_rules.group_id` 列を作成する。`migrate_device_groups()` が既存テナントに同じ変更を冪等に適用する。`migrate_dashboard_panel_config_group_id()` が `public.dashboard_panel_configs` に `group_id` 列とテナント+グループ+センサーキーのUNIQUE制約を追加する。後続タスクはこれらのテーブル・列を前提にSQLを書く。

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_db.py` の末尾に追記:

```python
def test_create_tenant_schema_includes_device_groups():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.database.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        create_tenant_schema("123e4567-e89b-12d3-a456-426614174000")
    sql_calls = " ".join(str(c) for c in mock_conn.execute.call_args_list)
    assert "device_groups" in sql_calls
    assert "devices" in sql_calls and "group_id" in sql_calls


def test_migrate_device_groups_alters_each_active_tenant():
    from app.database import migrate_device_groups

    tenant_row = MagicMock()
    tenant_row.id = "123e4567-e89b-12d3-a456-426614174000"

    list_conn = MagicMock()
    list_conn.__enter__ = lambda s: list_conn
    list_conn.__exit__ = MagicMock(return_value=False)
    list_conn.execute.return_value.fetchall.return_value = [tenant_row]

    alter_conn = MagicMock()
    alter_conn.__enter__ = lambda s: alter_conn
    alter_conn.__exit__ = MagicMock(return_value=False)

    with patch("app.database.engine") as mock_engine:
        mock_engine.connect.side_effect = [list_conn, alter_conn]
        migrate_device_groups()

    sql_calls = " ".join(str(c) for c in alter_conn.execute.call_args_list)
    assert "device_groups" in sql_calls
    assert "ADD COLUMN IF NOT EXISTS group_id" in sql_calls


def test_migrate_dashboard_panel_config_group_id_executes():
    from app.database import migrate_dashboard_panel_config_group_id
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.database.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        migrate_dashboard_panel_config_group_id()
    sql_calls = " ".join(str(c) for c in mock_conn.execute.call_args_list)
    assert "ADD COLUMN IF NOT EXISTS group_id" in sql_calls
    assert "dashboard_panel_configs_tenant_group_sensor_key_key" in sql_calls
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py -v`
Expected: `test_migrate_device_groups_alters_each_active_tenant` と `test_migrate_dashboard_panel_config_group_id_executes` が `ImportError`（関数未定義）で FAIL。`test_create_tenant_schema_includes_device_groups` は `device_groups` が見つからず FAIL。

- [ ] **Step 3: `create_tenant_schema()` に device_groups テーブルと group_id 列を追加する**

`core-api/app/database.py` の `create_tenant_schema()` 内、`users` テーブルCREATEの直後・`devices` テーブルCREATEの直前に挿入:

```python
        conn.execute(text(f'''
            CREATE TABLE IF NOT EXISTS "{schema}".device_groups (
                id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                name        VARCHAR(100) NOT NULL,
                description TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (name)
            )
        '''))
```

`devices` テーブルCREATEの `fw_version VARCHAR(100),` の行の直後に列を追加:

```python
                fw_version VARCHAR(100),
                group_id UUID REFERENCES "{schema}".device_groups(id) ON DELETE SET NULL,
```

`alert_rules` テーブルCREATEの `device_id VARCHAR(255),` の行の直後に列を追加:

```python
                device_id VARCHAR(255),
                group_id UUID,
```

既存の `idx_alert_rules_device` インデックス作成の直後に、`devices.group_id` と `alert_rules.group_id` 用インデックスを追加:

```python
        conn.execute(text(f'''
            CREATE INDEX IF NOT EXISTS idx_devices_group
            ON "{schema}".devices(group_id) WHERE group_id IS NOT NULL
        '''))
        conn.execute(text(f'''
            CREATE INDEX IF NOT EXISTS idx_alert_rules_group
            ON "{schema}".alert_rules(group_id) WHERE group_id IS NOT NULL
        '''))
```

- [ ] **Step 4: `migrate_device_groups()` を追加する**

`core-api/app/database.py` の `migrate_totp_columns()` の後に追加:

```python
def migrate_device_groups() -> None:
    """既存テナントに device_groups テーブルと devices/alert_rules の group_id 列を追加する（べき等）。"""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id FROM tenants WHERE status != 'deleted'")).fetchall()
    for row in rows:
        schema = f"tenant_{str(row.id).replace('-', '_')}"
        with engine.connect() as conn:
            conn.execute(text(f'''
                CREATE TABLE IF NOT EXISTS "{schema}".device_groups (
                    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                    name        VARCHAR(100) NOT NULL,
                    description TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (name)
                )
            '''))
            conn.execute(text(f'''
                ALTER TABLE "{schema}".devices
                ADD COLUMN IF NOT EXISTS group_id UUID
                REFERENCES "{schema}".device_groups(id) ON DELETE SET NULL
            '''))
            conn.execute(text(f'''
                ALTER TABLE "{schema}".alert_rules
                ADD COLUMN IF NOT EXISTS group_id UUID
            '''))
            conn.execute(text(f'''
                CREATE INDEX IF NOT EXISTS idx_devices_group
                ON "{schema}".devices(group_id) WHERE group_id IS NOT NULL
            '''))
            conn.execute(text(f'''
                CREATE INDEX IF NOT EXISTS idx_alert_rules_group
                ON "{schema}".alert_rules(group_id) WHERE group_id IS NOT NULL
            '''))
            conn.commit()
```

- [ ] **Step 5: `migrate_dashboard_panel_config_group_id()` を追加する**

`core-api/app/database.py` の `migrate_dashboard_panel_configs()` の後に追加:

```python
def migrate_dashboard_panel_config_group_id() -> None:
    """dashboard_panel_configs に group_id 列を追加し、UNIQUE制約を group_id 込みに張り替える（べき等）。"""
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE dashboard_panel_configs
            ADD COLUMN IF NOT EXISTS group_id UUID
        """))
        conn.execute(text("""
            ALTER TABLE dashboard_panel_configs
            DROP CONSTRAINT IF EXISTS dashboard_panel_configs_tenant_id_sensor_key_key
        """))
        conn.execute(text("""
            DO $$
            BEGIN
              IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'dashboard_panel_configs_tenant_group_sensor_key_key'
              ) THEN
                ALTER TABLE dashboard_panel_configs
                ADD CONSTRAINT dashboard_panel_configs_tenant_group_sensor_key_key
                UNIQUE (tenant_id, group_id, sensor_key);
              END IF;
            END $$;
        """))
        conn.commit()
```

- [ ] **Step 6: `DashboardPanelConfig` モデルを更新する**

`core-api/app/models/public.py` の `DashboardPanelConfig` を変更:

```python
class DashboardPanelConfig(Base):
    __tablename__ = "dashboard_panel_configs"
    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id  = Column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    group_id   = Column(UUID(as_uuid=True), nullable=True)
    sensor_key = Column(String(64), nullable=False)
    panel_type = Column(String(20), nullable=False, default="timeseries")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("tenant_id", "group_id", "sensor_key"),)
```

- [ ] **Step 7: main.py にマイグレーションを登録する**

`core-api/app/main.py` のimport行を変更:

```python
from app.database import migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id
```

`on_startup()` 内のタプルを変更:

```python
    for migrate in (migrate_add_grafana_org_id, migrate_add_device_name, migrate_add_provisioning_token_id, migrate_add_public_token, migrate_add_token_version, migrate_totp_columns, migrate_dashboard_panel_configs, migrate_create_audit_logs, migrate_device_groups, migrate_dashboard_panel_config_group_id):
```

- [ ] **Step 8: テストを実行して成功を確認する**

Run: `cd core-api && python -m pytest tests/test_db.py -v`
Expected: 全テストPASS

- [ ] **Step 9: コミット**

```bash
git add core-api/app/database.py core-api/app/main.py core-api/app/models/public.py core-api/tests/test_db.py
git commit -m "feat(device-groups): デバイスグループのデータモデルとマイグレーションを追加"
```

---

### Task 2: グループCRUD API（サービス層 + プラットフォーム/テナントポータル両ルーター）

**Files:**
- Create: `core-api/app/schemas/device_group.py`
- Create: `core-api/app/services/device_groups.py`
- Create: `core-api/app/routers/device_groups.py`
- Modify: `core-api/app/routers/tenant_portal.py`
- Modify: `core-api/app/main.py`
- Test: `core-api/tests/test_device_groups.py`
- Test: `core-api/tests/test_device_groups_api.py`

**Interfaces:**
- Consumes: Task 1 の `"{schema}".device_groups` テーブル、`"{schema}".alert_rules.group_id` 列。
- Produces: `app.services.device_groups` の `list_groups(schema) -> list[dict]`、`create_group(schema, name, description) -> dict`、`update_group(schema, group_id, name, description) -> dict`、`delete_group(schema, group_id) -> None`、例外 `GroupNotFoundError` / `GroupNameConflictError` / `GroupInUseError(rules)` / `DeviceNotFoundError`。これらは Task 3〜5 で再利用する。各 dict は `{"id": str, "name": str, "description": str|None, "created_at": str|None}` 形式。

- [ ] **Step 1: スキーマを書く**

Create `core-api/app/schemas/device_group.py`:

```python
from typing import Optional
from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None


class GroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None


class GroupOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    created_at: Optional[str] = None
```

- [ ] **Step 2: 失敗するサービス層テストを書く**

Create `core-api/tests/test_device_groups.py`:

```python
from unittest.mock import patch, MagicMock
import pytest

from app.services.device_groups import (
    GroupInUseError,
    GroupNameConflictError,
    GroupNotFoundError,
    create_group,
    delete_group,
    list_groups,
    update_group,
)

SCHEMA = "tenant_11111111_1111_1111_1111_111111111111"


def _conn():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def test_list_groups_returns_rows():
    row = MagicMock(id="g1", name="拠点A", description="説明", created_at=None)
    conn = _conn()
    conn.execute.return_value.fetchall.return_value = [row]
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        result = list_groups(SCHEMA)
    assert result == [{"id": "g1", "name": "拠点A", "description": "説明", "created_at": None}]


def test_create_group_returns_new_group():
    row = MagicMock(id="g1", name="拠点A", description=None, created_at=None)
    conn = _conn()
    conn.execute.return_value.fetchone.return_value = row
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        result = create_group(SCHEMA, "拠点A", None)
    assert result["name"] == "拠点A"
    assert conn.commit.called


def test_create_group_duplicate_name_raises_conflict():
    from sqlalchemy.exc import IntegrityError
    conn = _conn()
    conn.execute.side_effect = IntegrityError("stmt", {}, Exception("dup"))
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        with pytest.raises(GroupNameConflictError):
            create_group(SCHEMA, "拠点A", None)


def test_update_group_not_found_raises():
    conn = _conn()
    conn.execute.return_value.fetchone.return_value = None
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        with pytest.raises(GroupNotFoundError):
            update_group(SCHEMA, "missing-id", "新名前", None)


def test_delete_group_blocked_by_alert_rules():
    conn = _conn()
    existing_row = MagicMock(id="g1")
    rule_row = MagicMock(id="r1", sensor_key="temperature", condition="above", severity="warning")
    conn.execute.return_value.fetchone.side_effect = [existing_row]
    conn.execute.return_value.fetchall.return_value = [rule_row]
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        with pytest.raises(GroupInUseError) as excinfo:
            delete_group(SCHEMA, "g1")
    assert excinfo.value.rules == [{"id": "r1", "sensor_key": "temperature", "condition": "above", "severity": "warning"}]


def test_delete_group_succeeds_when_unreferenced():
    conn = _conn()
    existing_row = MagicMock(id="g1")
    conn.execute.return_value.fetchone.side_effect = [existing_row]
    conn.execute.return_value.fetchall.return_value = []
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        delete_group(SCHEMA, "g1")
    assert conn.commit.called
```

- [ ] **Step 3: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_device_groups.py -v`
Expected: `ModuleNotFoundError: No module named 'app.services.device_groups'` で全件FAIL

- [ ] **Step 4: サービス層を実装する**

Create `core-api/app/services/device_groups.py`:

```python
import uuid
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.database import engine


class GroupNotFoundError(Exception):
    pass


class GroupNameConflictError(Exception):
    pass


class DeviceNotFoundError(Exception):
    pass


class GroupInUseError(Exception):
    def __init__(self, rules: list[dict]):
        self.rules = rules
        super().__init__(f"Group is referenced by {len(rules)} alert rule(s)")


def _row_to_dict(row) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "description": row.description,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_groups(schema: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(f'''
            SELECT id, name, description, created_at
            FROM "{schema}".device_groups
            ORDER BY created_at DESC
        ''')).fetchall()
    return [_row_to_dict(r) for r in rows]


def create_group(schema: str, name: str, description: str | None) -> dict:
    group_id = str(uuid.uuid4())
    with engine.connect() as conn:
        try:
            conn.execute(text(f'''
                INSERT INTO "{schema}".device_groups (id, name, description)
                VALUES (:id, :name, :description)
            '''), {"id": group_id, "name": name, "description": description})
            conn.commit()
        except IntegrityError:
            raise GroupNameConflictError(name)
        row = conn.execute(text(f'''
            SELECT id, name, description, created_at
            FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
    return _row_to_dict(row)


def update_group(schema: str, group_id: str, name: str | None, description: str | None) -> dict:
    updates = {}
    if name is not None:
        updates["name"] = name
    if description is not None:
        updates["description"] = description
    with engine.connect() as conn:
        existing = conn.execute(text(f'''
            SELECT id FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
        if not existing:
            raise GroupNotFoundError(group_id)
        if updates:
            set_clauses = ", ".join(f"{col} = :{col}" for col in updates)
            try:
                conn.execute(text(f'''
                    UPDATE "{schema}".device_groups SET {set_clauses} WHERE id = :id
                '''), {"id": group_id, **updates})
                conn.commit()
            except IntegrityError:
                raise GroupNameConflictError(updates.get("name", ""))
        row = conn.execute(text(f'''
            SELECT id, name, description, created_at
            FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
    return _row_to_dict(row)


def delete_group(schema: str, group_id: str) -> None:
    with engine.connect() as conn:
        existing = conn.execute(text(f'''
            SELECT id FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
        if not existing:
            raise GroupNotFoundError(group_id)
        blocking = conn.execute(text(f'''
            SELECT id, sensor_key, condition, severity
            FROM "{schema}".alert_rules
            WHERE group_id = :gid AND is_active = TRUE
        '''), {"gid": group_id}).fetchall()
        if blocking:
            raise GroupInUseError([
                {"id": str(r.id), "sensor_key": r.sensor_key, "condition": r.condition, "severity": r.severity}
                for r in blocking
            ])
        conn.execute(text(f'DELETE FROM "{schema}".device_groups WHERE id = :id'), {"id": group_id})
        conn.commit()


def assign_device_group(schema: str, device_id: str, group_id: str | None) -> str | None:
    """デバイスの所属グループを変更し、変更前の group_id を返す。"""
    with engine.connect() as conn:
        row = conn.execute(text(f'''
            SELECT group_id FROM "{schema}".devices WHERE device_id = :did
        '''), {"did": device_id}).fetchone()
        if not row:
            raise DeviceNotFoundError(device_id)
        old_group_id = str(row.group_id) if row.group_id else None
        if group_id is not None:
            exists = conn.execute(text(f'''
                SELECT id FROM "{schema}".device_groups WHERE id = :gid
            '''), {"gid": group_id}).fetchone()
            if not exists:
                raise GroupNotFoundError(group_id)
        conn.execute(text(f'''
            UPDATE "{schema}".devices SET group_id = :gid WHERE device_id = :did
        '''), {"gid": group_id, "did": device_id})
        conn.commit()
    return old_group_id


def list_group_device_ids(schema: str, group_id: str) -> list[str]:
    with engine.connect() as conn:
        group = conn.execute(text(f'''
            SELECT id FROM "{schema}".device_groups WHERE id = :id
        '''), {"id": group_id}).fetchone()
        if not group:
            raise GroupNotFoundError(group_id)
        rows = conn.execute(text(f'''
            SELECT device_id FROM "{schema}".devices WHERE group_id = :gid
        '''), {"gid": group_id}).fetchall()
    return [r.device_id for r in rows]
```

- [ ] **Step 5: サービス層テストを実行して成功を確認する**

Run: `cd core-api && python -m pytest tests/test_device_groups.py -v`
Expected: 全テストPASS

- [ ] **Step 6: 失敗するAPIテストを書く**

Create `core-api/tests/test_device_groups_api.py`:

```python
from unittest.mock import patch, MagicMock
import uuid
from app.main import app
from fastapi.testclient import TestClient
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token(role: str = "admin"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def _conn():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def test_platform_list_groups_empty():
    conn = _conn()
    conn.execute.return_value.fetchall.return_value = []
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.get(f"/tenants/{TENANT_ID}/groups", headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_platform_create_group_success():
    conn = _conn()
    row = MagicMock(id=uuid.uuid4(), name="拠点A", description=None, created_at=None)
    conn.execute.return_value.fetchone.return_value = row
    with patch("app.services.device_groups.engine") as mock_engine, \
         patch("app.routers.device_groups.log_audit") as mock_log:
        mock_engine.connect.return_value = conn
        resp = client.post(
            f"/tenants/{TENANT_ID}/groups", json={"name": "拠点A"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 201
    assert resp.json()["name"] == "拠点A"
    mock_log.assert_called_once()


def test_platform_create_group_duplicate_name_returns_409():
    from sqlalchemy.exc import IntegrityError
    conn = _conn()
    conn.execute.side_effect = IntegrityError("stmt", {}, Exception("dup"))
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.post(
            f"/tenants/{TENANT_ID}/groups", json={"name": "拠点A"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 409


def test_platform_delete_group_in_use_returns_409_with_rules():
    conn = _conn()
    existing_row = MagicMock(id="g1")
    rule_row = MagicMock(id="r1", sensor_key="temperature", condition="above", severity="warning")
    conn.execute.return_value.fetchone.side_effect = [existing_row]
    conn.execute.return_value.fetchall.return_value = [rule_row]
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.delete(f"/tenants/{TENANT_ID}/groups/g1", headers={"Authorization": f"Bearer {_platform_token()}"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["alert_rules"][0]["sensor_key"] == "temperature"


def test_platform_groups_requires_auth():
    resp = client.get(f"/tenants/{TENANT_ID}/groups")
    assert resp.status_code == 403


def test_portal_list_groups_empty():
    conn = _conn()
    conn.execute.return_value.fetchall.return_value = []
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.get("/tenant-portal/me/groups", cookies={"iot_token": _tenant_token("viewer")})
    assert resp.status_code == 200
    assert resp.json() == []


def test_portal_create_group_requires_admin():
    resp = client.post(
        "/tenant-portal/me/groups", json={"name": "拠点A"},
        cookies={"iot_token": _tenant_token("operator")},
    )
    assert resp.status_code == 403


def test_portal_create_group_success():
    conn = _conn()
    row = MagicMock(id=uuid.uuid4(), name="拠点A", description=None, created_at=None)
    conn.execute.return_value.fetchone.return_value = row
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        resp = client.post(
            "/tenant-portal/me/groups", json={"name": "拠点A"},
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 201
```

- [ ] **Step 7: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_device_groups_api.py -v`
Expected: 全件FAIL（`404 Not Found` — ルーター未登録）

- [ ] **Step 8: プラットフォームルーターを実装する**

Create `core-api/app/routers/device_groups.py`:

```python
import re
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.schemas.device_group import GroupCreate, GroupOut, GroupUpdate
from app.services.auth import verify_token
from app.services.audit import log_audit
from app.services.device_groups import (
    GroupInUseError,
    GroupNameConflictError,
    GroupNotFoundError,
    create_group,
    delete_group,
    list_groups,
    update_group,
)

router = APIRouter(prefix="/tenants/{tenant_id}/groups", tags=["device-groups"])
_bearer = HTTPBearer()


def _require_platform(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    payload = verify_token(creds.credentials)
    if not payload or payload.get("type") != "platform" or payload.get("token_type") == "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return payload


def _schema(tenant_id: str) -> str:
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', tenant_id.lower()):
        raise HTTPException(status_code=400, detail="Invalid tenant_id")
    return f"tenant_{tenant_id.replace('-', '_')}"


@router.get("", response_model=list[GroupOut])
def list_device_groups(tenant_id: str, _: dict = Depends(_require_platform)):
    return list_groups(_schema(tenant_id))


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_device_group(tenant_id: str, body: GroupCreate, payload: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    try:
        group = create_group(schema, body.name, body.description)
    except GroupNameConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    log_audit("platform", payload["sub"], payload["email"], "create_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group["id"],
              detail={"name": group["name"]})
    return group


@router.patch("/{group_id}", response_model=GroupOut)
def update_device_group(tenant_id: str, group_id: str, body: GroupUpdate, payload: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    try:
        group = update_group(schema, group_id, body.name, body.description)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    except GroupNameConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    log_audit("platform", payload["sub"], payload["email"], "update_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group_id,
              detail=body.model_dump(exclude_none=True))
    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device_group(tenant_id: str, group_id: str, payload: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    try:
        delete_group(schema, group_id)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    except GroupInUseError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "group_in_use", "alert_rules": e.rules})
    log_audit("platform", payload["sub"], payload["email"], "delete_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group_id)
```

`core-api/app/main.py` に登録（import行とinclude_router行を両方追加）:

```python
from app.routers import health, auth, mfa, tenants, provisioning, emqx, provisioning_tokens, alert_rules, emqx_events, firmware, stats, tenant_auth, tenant_mfa, tenant_users, tenant_devices, tenant_grafana, tenant_portal, public_access, platform, audit_logs, device_groups
```

```python
app.include_router(device_groups.router)
```

- [ ] **Step 9: テナントポータルルーターに追加する**

`core-api/app/routers/tenant_portal.py` の import部分に追加:

```python
from app.schemas.device_group import GroupCreate, GroupOut, GroupUpdate
from app.services.device_groups import (
    GroupInUseError,
    GroupNameConflictError,
    GroupNotFoundError,
    create_group,
    delete_group,
    list_groups,
    update_group,
)
```

ファイル末尾（`/dashboard/sensor-keys` の後）に追加:

```python
# ---------------------------------------------------------------------------
# デバイスグループ管理
# ---------------------------------------------------------------------------

@router.get("/me/groups", response_model=list[GroupOut])
def list_device_groups_portal(payload: dict = Depends(_require_tenant)):
    return list_groups(_schema(payload["tenant_id"]))


@router.post("/me/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_device_group_portal(body: GroupCreate, payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    try:
        group = create_group(_schema(tenant_id), body.name, body.description)
    except GroupNameConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    log_audit("tenant", payload["sub"], payload["email"], "create_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group["id"],
              detail={"name": group["name"]})
    return group


@router.patch("/me/groups/{group_id}", response_model=GroupOut)
def update_device_group_portal(group_id: str, body: GroupUpdate, payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    try:
        group = update_group(_schema(tenant_id), group_id, body.name, body.description)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    except GroupNameConflictError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    log_audit("tenant", payload["sub"], payload["email"], "update_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group_id,
              detail=body.model_dump(exclude_none=True))
    return group


@router.delete("/me/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device_group_portal(group_id: str, payload: dict = Depends(_require_admin)):
    tenant_id = payload["tenant_id"]
    try:
        delete_group(_schema(tenant_id), group_id)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    except GroupInUseError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"error": "group_in_use", "alert_rules": e.rules})
    log_audit("tenant", payload["sub"], payload["email"], "delete_device_group",
              tenant_id=tenant_id, resource_type="device_group", resource_id=group_id)
```

- [ ] **Step 10: テストを実行して成功を確認する**

Run: `cd core-api && python -m pytest tests/test_device_groups_api.py tests/test_device_groups.py -v`
Expected: 全テストPASS

- [ ] **Step 11: コミット**

```bash
git add core-api/app/schemas/device_group.py core-api/app/services/device_groups.py core-api/app/routers/device_groups.py core-api/app/routers/tenant_portal.py core-api/app/main.py core-api/tests/test_device_groups.py core-api/tests/test_device_groups_api.py
git commit -m "feat(device-groups): グループCRUD APIを追加（プラットフォーム/テナントポータル両対応）"
```

---

### Task 3: デバイスのグループ割当

**Files:**
- Modify: `core-api/app/routers/tenant_devices.py`
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_device_group_assignment.py`

**Interfaces:**
- Consumes: `app.services.device_groups.assign_device_group(schema, device_id, group_id) -> old_group_id`（Task 2）, `DeviceNotFoundError`, `GroupNotFoundError`
- Produces: `PATCH /tenants/{tenant_id}/devices/{device_id}` と `PATCH /tenant-portal/me/devices/{device_id}`（どちらも body `{"group_id": uuid|null}`）。`DeviceOut`（`tenant_devices.py`）と `/me/devices` のレスポンスに `group_id` フィールドが追加される。

- [ ] **Step 1: 失敗するテストを書く**

Create `core-api/tests/test_device_group_assignment.py`:

```python
from unittest.mock import patch, MagicMock
from app.main import app
from fastapi.testclient import TestClient
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token(role: str = "operator"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def _make_tenant():
    t = MagicMock()
    t.id = TENANT_ID
    t.status = "active"
    return t


def _device_row(group_id=None):
    from datetime import datetime, timezone
    row = MagicMock()
    row.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    row.device_id = "device-001"
    row.device_name = "センサー1"
    row.connection_status = "online"
    row.last_seen_at = None
    row.fw_version = None
    row.cert_not_after = None
    row.created_at = datetime(2026, 9, 8, tzinfo=timezone.utc)
    row.group_id = group_id
    return row


def test_platform_assign_device_group_success():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.tenant_devices.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_devices.engine") as mock_engine, \
         patch("app.routers.tenant_devices.log_audit") as mock_log:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = _make_tenant()

        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.side_effect = [
            MagicMock(group_id=None),          # assign_device_group: 現在のgroup_id取得
            MagicMock(id="g1"),                # assign_device_group: グループ存在確認
        ]
        mock_svc_engine.connect.return_value = svc_conn

        router_conn = MagicMock()
        router_conn.__enter__ = lambda s: router_conn
        router_conn.__exit__ = MagicMock(return_value=False)
        router_conn.execute.return_value.fetchone.return_value = _device_row(group_id="g1")
        mock_engine.connect.return_value = router_conn

        resp = client.patch(
            f"/tenants/{TENANT_ID}/devices/device-001",
            json={"group_id": "g1"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    assert resp.json()["group_id"] == "g1"
    mock_log.assert_called_once()
    _, kwargs = mock_log.call_args
    assert kwargs["detail"] == {"old_group_id": None, "new_group_id": "g1"}


def test_platform_assign_device_group_device_not_found():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.tenant_devices.SessionLocal") as mock_sl:
        mock_sl.return_value.__enter__.return_value.query.return_value.filter.return_value.first.return_value = _make_tenant()
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = None
        mock_svc_engine.connect.return_value = svc_conn

        resp = client.patch(
            f"/tenants/{TENANT_ID}/devices/missing-device",
            json={"group_id": "g1"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_portal_assign_device_group_requires_operator_or_admin():
    resp = client.patch(
        "/tenant-portal/me/devices/device-001",
        json={"group_id": "g1"},
        cookies={"iot_token": _tenant_token("viewer")},
    )
    assert resp.status_code == 403


def test_portal_assign_device_group_clears_group():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.tenant_portal.engine") as mock_engine, \
         patch("app.routers.tenant_portal.log_audit") as mock_log:
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = MagicMock(group_id="g1")
        mock_svc_engine.connect.return_value = svc_conn

        router_conn = MagicMock()
        router_conn.__enter__ = lambda s: router_conn
        router_conn.__exit__ = MagicMock(return_value=False)
        router_conn.execute.return_value.fetchone.return_value = _device_row(group_id=None)
        mock_engine.connect.return_value = router_conn

        resp = client.patch(
            "/tenant-portal/me/devices/device-001",
            json={"group_id": None},
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 200
    assert resp.json()["group_id"] is None
    _, kwargs = mock_log.call_args
    assert kwargs["detail"] == {"old_group_id": "g1", "new_group_id": None}
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_device_group_assignment.py -v`
Expected: `405 Method Not Allowed`（PATCH未定義）で全件FAIL

- [ ] **Step 3: `tenant_devices.py` にPATCHエンドポイントを追加する**

`core-api/app/routers/tenant_devices.py` の import部分に追加:

```python
from app.services.audit import log_audit
from app.services.device_groups import DeviceNotFoundError, GroupNotFoundError, assign_device_group
```

`DeviceOut` に `group_id` を追加:

```python
class DeviceOut(BaseModel):
    id: str
    device_id: str
    device_name: Optional[str] = None
    connection_status: str
    last_seen_at: Optional[str] = None
    fw_version: Optional[str] = None
    cert_not_after: Optional[str] = None
    created_at: str
    group_id: Optional[str] = None
```

`list_tenant_devices` のSELECTと構築部分に `group_id` を追加:

```python
        rows = conn.execute(
            text(f'''
                SELECT id, device_id, device_name, connection_status, last_seen_at,
                       fw_version, cert_not_after, created_at, group_id
                FROM "{schema}".devices
                ORDER BY created_at DESC
                LIMIT 1000
            ''')
        ).fetchall()
    return [
        DeviceOut(
            id=str(r.id),
            device_id=r.device_id,
            device_name=r.device_name,
            connection_status=r.connection_status,
            last_seen_at=r.last_seen_at.isoformat() if r.last_seen_at else None,
            fw_version=r.fw_version,
            cert_not_after=r.cert_not_after.isoformat() if r.cert_not_after else None,
            created_at=r.created_at.isoformat(),
            group_id=str(r.group_id) if r.group_id else None,
        )
        for r in rows
    ]
```

`delete_tenant_device` の後に新しいエンドポイントを追加:

```python
class DeviceUpdateBody(BaseModel):
    group_id: Optional[str] = None


@router.patch("/{device_id}", response_model=DeviceOut)
def update_tenant_device(tenant_id: UUID, device_id: str, body: DeviceUpdateBody, payload: dict = Depends(_require_platform)):
    tenant_id_str = str(tenant_id)
    _get_active_tenant(tenant_id_str)
    schema = f"tenant_{tenant_id_str.replace('-', '_')}"
    try:
        old_group_id = assign_device_group(schema, device_id, body.group_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    log_audit("platform", payload["sub"], payload["email"], "assign_device_group",
              tenant_id=tenant_id_str, resource_type="device", resource_id=device_id,
              detail={"old_group_id": old_group_id, "new_group_id": body.group_id})
    with engine.connect() as conn:
        row = conn.execute(text(f'''
            SELECT id, device_id, device_name, connection_status, last_seen_at,
                   fw_version, cert_not_after, created_at, group_id
            FROM "{schema}".devices WHERE device_id = :did
        '''), {"did": device_id}).fetchone()
    return DeviceOut(
        id=str(row.id), device_id=row.device_id, device_name=row.device_name,
        connection_status=row.connection_status,
        last_seen_at=row.last_seen_at.isoformat() if row.last_seen_at else None,
        fw_version=row.fw_version,
        cert_not_after=row.cert_not_after.isoformat() if row.cert_not_after else None,
        created_at=row.created_at.isoformat(),
        group_id=str(row.group_id) if row.group_id else None,
    )
```

- [ ] **Step 4: `tenant_portal.py` にPATCHエンドポイントを追加する**

import部分に追加:

```python
from app.services.device_groups import DeviceNotFoundError, GroupNotFoundError, assign_device_group
```

`/me/devices` の GET のSELECT・レスポンス構築に `group_id` を追加:

```python
        rows = conn.execute(text(f'''
            SELECT id, device_id, device_name, connection_status, last_seen_at,
                   fw_version, cert_not_after, created_at, group_id
            FROM "{schema}".devices
            ORDER BY created_at DESC
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
        }
        for r in rows
    ]
```

`delete_device` エンドポイントの直後に追加:

```python
class DeviceUpdateBody(BaseModel):
    group_id: Optional[str] = None


@router.patch("/me/devices/{device_id}")
def update_device(device_id: str, body: DeviceUpdateBody, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    try:
        old_group_id = assign_device_group(schema, device_id, body.group_id)
    except DeviceNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    log_audit("tenant", payload["sub"], payload["email"], "assign_device_group",
              tenant_id=tenant_id, resource_type="device", resource_id=device_id,
              detail={"old_group_id": old_group_id, "new_group_id": body.group_id})
    with engine.connect() as conn:
        row = conn.execute(text(f'''
            SELECT id, device_id, device_name, connection_status, last_seen_at,
                   fw_version, cert_not_after, created_at, group_id
            FROM "{schema}".devices WHERE device_id = :did
        '''), {"did": device_id}).fetchone()
    return {
        "id": str(row.id), "device_id": row.device_id, "device_name": row.device_name or row.device_id,
        "connection_status": row.connection_status,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
        "fw_version": row.fw_version,
        "cert_not_after": row.cert_not_after.isoformat() if row.cert_not_after else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "group_id": str(row.group_id) if row.group_id else None,
    }
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `cd core-api && python -m pytest tests/test_device_group_assignment.py -v`
Expected: 全テストPASS

- [ ] **Step 6: 既存の関連テストが壊れていないか確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_devices.py tests/test_tenant_portal_public_access.py -v`
Expected: 全テストPASS（`group_id` フィールド追加が既存テストの `assert data[0] == {...}` のような完全一致比較を壊していないことを確認。壊れていた場合は該当アサーションに `group_id` を追記する）

- [ ] **Step 7: コミット**

```bash
git add core-api/app/routers/tenant_devices.py core-api/app/routers/tenant_portal.py core-api/tests/test_device_group_assignment.py
git commit -m "feat(device-groups): デバイスのグループ割当APIを追加"
```

---

### Task 4: アラートルールのグループ対象化

**Files:**
- Modify: `core-api/app/routers/alert_rules.py`
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_alert_rules_group.py`

**Interfaces:**
- Consumes: Task 1 の `alert_rules.group_id` 列
- Produces: `AlertRuleCreate` / `AlertRuleUpdate` / `AlertRuleOut` に `group_id: Optional[str]` が追加され、`device_id` と `group_id` の同時指定は422になる（作成時はpydanticバリデータ、更新時は既存レコードとのマージ結果をルーターでチェック）

- [ ] **Step 1: 失敗するテストを書く**

Create `core-api/tests/test_alert_rules_group.py`:

```python
from unittest.mock import patch, MagicMock
from app.main import app
from fastapi.testclient import TestClient
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token(role: str = "operator"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def test_platform_create_alert_rule_rejects_both_device_and_group():
    resp = client.post(
        f"/tenants/{TENANT_ID}/alert-rules",
        json={"device_id": "dev-1", "group_id": "g1", "sensor_key": "temperature", "condition": "above", "threshold": 30},
        headers={"Authorization": f"Bearer {_platform_token()}"},
    )
    assert resp.status_code == 422


def test_platform_create_alert_rule_with_group_id_succeeds():
    with patch("app.routers.alert_rules.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        resp = client.post(
            f"/tenants/{TENANT_ID}/alert-rules",
            json={"group_id": "g1", "sensor_key": "temperature", "condition": "above", "threshold": 30},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 201
    assert resp.json()["group_id"] == "g1"
    assert mock_db.execute.called


def test_platform_update_alert_rule_rejects_conflicting_group_when_device_already_set():
    row = MagicMock(id="r1", device_id="dev-1", group_id=None)
    with patch("app.routers.alert_rules.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.execute.return_value.fetchone.return_value = row
        resp = client.patch(
            f"/tenants/{TENANT_ID}/alert-rules/r1",
            json={"group_id": "g1"},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 422


def test_portal_create_alert_rule_rejects_both_device_and_group():
    resp = client.post(
        "/tenant-portal/me/alert-rules",
        json={"device_id": "dev-1", "group_id": "g1", "sensor_key": "temperature", "condition": "above", "threshold": 30},
        cookies={"iot_token": _tenant_token()},
    )
    assert resp.status_code == 422


def test_portal_create_alert_rule_with_group_id_succeeds():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        resp = client.post(
            "/tenant-portal/me/alert-rules",
            json={"group_id": "g1", "sensor_key": "temperature", "condition": "above", "threshold": 30},
            cookies={"iot_token": _tenant_token()},
        )
    assert resp.status_code == 201
    assert resp.json()["group_id"] == "g1"
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_alert_rules_group.py -v`
Expected: `group_id` 未定義のため422バリデーションが発火せず、あるいはレスポンスに `group_id` が含まれずFAIL

- [ ] **Step 3: `alert_rules.py`（プラットフォーム）を更新する**

`core-api/app/routers/alert_rules.py` の import に `model_validator` を追加:

```python
from pydantic import BaseModel, model_validator
```

`AlertRuleCreate` / `AlertRuleUpdate` / `AlertRuleOut` を変更:

```python
class AlertRuleCreate(BaseModel):
    device_id: Optional[str] = None
    group_id: Optional[str] = None
    sensor_key: str
    condition: Literal["above", "below", "equal", "device_offline"]
    threshold: Optional[float] = None
    trigger_mode: Literal["consecutive", "duration", "consecutive_and_duration"] = "consecutive"
    consecutive_count: int = 3
    duration_sec: int = 60
    severity: Literal["info", "warning", "critical"] = "warning"
    notify_emails: list[str] = []

    @model_validator(mode="after")
    def _validate_exclusive_target(self):
        if self.device_id and self.group_id:
            raise ValueError("device_id and group_id are mutually exclusive")
        return self

class AlertRuleUpdate(BaseModel):
    device_id: Optional[str] = None
    group_id: Optional[str] = None
    sensor_key: Optional[str] = None
    condition: Optional[Literal["above", "below", "equal", "device_offline"]] = None
    threshold: Optional[float] = None
    trigger_mode: Optional[Literal["consecutive", "duration", "consecutive_and_duration"]] = None
    consecutive_count: Optional[int] = None
    duration_sec: Optional[int] = None
    severity: Optional[Literal["info", "warning", "critical"]] = None
    notify_emails: Optional[list[str]] = None

    @model_validator(mode="after")
    def _validate_exclusive_target(self):
        if self.device_id and self.group_id:
            raise ValueError("device_id and group_id are mutually exclusive")
        return self

class AlertRuleOut(BaseModel):
    id: str
    device_id: Optional[str]
    group_id: Optional[str]
    sensor_key: str
    condition: str
    threshold: Optional[float]
    trigger_mode: str
    consecutive_count: int
    duration_sec: int
    severity: str
    notify_emails: list[str]
    is_active: bool
```

`create_alert_rule` のINSERT文とレスポンス構築に `group_id` を追加:

```python
        db.execute(
            text(f'''
                INSERT INTO "{schema}".alert_rules
                  (id, device_id, group_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails)
                VALUES (:id, :did, :gid, :sk, :cond, :thr, :tm, :cc, :ds, :sev, :emails)
            ''').bindparams(bindparam("emails", type_=ARRAY(SaString))),
            {
                "id": rule_id, "did": body.device_id, "gid": body.group_id, "sk": body.sensor_key,
                "cond": body.condition, "thr": body.threshold, "tm": body.trigger_mode,
                "cc": body.consecutive_count, "ds": body.duration_sec,
                "sev": body.severity, "emails": list(body.notify_emails),
            }
        )
        db.commit()
    return AlertRuleOut(
        id=rule_id, device_id=body.device_id, group_id=body.group_id, sensor_key=body.sensor_key,
        condition=body.condition, threshold=body.threshold,
        trigger_mode=body.trigger_mode, consecutive_count=body.consecutive_count,
        duration_sec=body.duration_sec, severity=body.severity,
        notify_emails=body.notify_emails, is_active=True,
    )
```

`list_alert_rules` を変更:

```python
@router.get("/{tenant_id}/alert-rules", response_model=list[AlertRuleOut])
def list_alert_rules(tenant_id: str, _: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    with SessionLocal() as db:
        rows = db.execute(text(f'''
            SELECT id, device_id, group_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails, is_active
            FROM "{schema}".alert_rules WHERE is_active = TRUE
        ''')).fetchall()
    return [AlertRuleOut(
        id=str(r.id), device_id=r.device_id, group_id=str(r.group_id) if r.group_id else None,
        sensor_key=r.sensor_key,
        condition=r.condition, threshold=float(r.threshold) if r.threshold is not None else None,
        trigger_mode=r.trigger_mode, consecutive_count=r.consecutive_count,
        duration_sec=r.duration_sec, severity=r.severity,
        notify_emails=list(r.notify_emails) if r.notify_emails else [],
        is_active=r.is_active,
    ) for r in rows]
```

`update_alert_rule` を、既存行の `device_id`/`group_id` を取得したうえで排他チェックするよう変更:

```python
@router.patch("/{tenant_id}/alert-rules/{rule_id}", response_model=AlertRuleOut)
def update_alert_rule(tenant_id: str, rule_id: str, body: AlertRuleUpdate, _: dict = Depends(_require_platform)):
    schema = _schema(tenant_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    with SessionLocal() as db:
        row = db.execute(text(f'''
            SELECT id, device_id, group_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails, is_active
            FROM "{schema}".alert_rules WHERE id = :rid AND is_active = TRUE
        '''), {"rid": rule_id}).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Rule not found")
        final_device_id = updates.get("device_id", row.device_id)
        final_group_id = updates.get("group_id", row.group_id)
        if final_device_id and final_group_id:
            raise HTTPException(status_code=422, detail="device_id and group_id are mutually exclusive")
        set_clauses = ", ".join(
            f"{col} = :{col}" for col in updates if col != "notify_emails"
        )
        params = {"rid": rule_id, **{k: v for k, v in updates.items() if k != "notify_emails"}}
        if "notify_emails" in updates:
            set_clauses = (set_clauses + ", notify_emails = :notify_emails").lstrip(", ")
            stmt = text(f'UPDATE "{schema}".alert_rules SET {set_clauses} WHERE id = :rid'
                        ).bindparams(bindparam("notify_emails", type_=ARRAY(SaString)))
            params["notify_emails"] = updates["notify_emails"]
        else:
            stmt = text(f'UPDATE "{schema}".alert_rules SET {set_clauses} WHERE id = :rid')
        db.execute(stmt, params)
        db.commit()
        updated = db.execute(text(f'''
            SELECT id, device_id, group_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails, is_active
            FROM "{schema}".alert_rules WHERE id = :rid
        '''), {"rid": rule_id}).fetchone()
    return AlertRuleOut(
        id=str(updated.id), device_id=updated.device_id, group_id=str(updated.group_id) if updated.group_id else None,
        sensor_key=updated.sensor_key,
        condition=updated.condition, threshold=float(updated.threshold) if updated.threshold is not None else None,
        trigger_mode=updated.trigger_mode, consecutive_count=updated.consecutive_count,
        duration_sec=updated.duration_sec, severity=updated.severity,
        notify_emails=list(updated.notify_emails) if updated.notify_emails else [],
        is_active=updated.is_active,
    )
```

- [ ] **Step 4: `tenant_portal.py`（テナントポータル）を同様に更新する**

import に `model_validator` を追加:

```python
from pydantic import BaseModel, Field, field_validator, model_validator
```

`AlertRuleCreate` / `AlertRuleUpdate`（tenant_portal版）を変更:

```python
class AlertRuleCreate(BaseModel):
    device_id: Optional[str] = None
    group_id: Optional[str] = None
    sensor_key: str
    condition: Literal["above", "below", "equal", "device_offline"]
    threshold: Optional[float] = None
    trigger_mode: Literal["consecutive", "duration", "consecutive_and_duration"] = "consecutive"
    consecutive_count: int = 3
    duration_sec: int = 60
    severity: Literal["info", "warning", "critical"] = "warning"
    notify_emails: list[str] = []

    @model_validator(mode="after")
    def _validate_exclusive_target(self):
        if self.device_id and self.group_id:
            raise ValueError("device_id and group_id are mutually exclusive")
        return self

class AlertRuleUpdate(BaseModel):
    device_id: Optional[str] = None
    group_id: Optional[str] = None
    sensor_key: Optional[str] = None
    condition: Optional[Literal["above", "below", "equal", "device_offline"]] = None
    threshold: Optional[float] = None
    trigger_mode: Optional[Literal["consecutive", "duration", "consecutive_and_duration"]] = None
    consecutive_count: Optional[int] = None
    duration_sec: Optional[int] = None
    severity: Optional[Literal["info", "warning", "critical"]] = None
    notify_emails: Optional[list[str]] = None

    @model_validator(mode="after")
    def _validate_exclusive_target(self):
        if self.device_id and self.group_id:
            raise ValueError("device_id and group_id are mutually exclusive")
        return self
```

`_ALLOWED_ALERT_COLS` に `"group_id"` を追加:

```python
    _ALLOWED_ALERT_COLS = {"sensor_key", "condition", "threshold", "trigger_mode",
                           "consecutive_count", "duration_sec", "severity", "device_id", "group_id"}
```

`create_alert_rule`（tenant_portal版）を変更:

```python
@router.post("/me/alert-rules", status_code=status.HTTP_201_CREATED)
def create_alert_rule(body: AlertRuleCreate, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    rule_id = str(uuid_lib.uuid4())
    with SessionLocal() as db:
        db.execute(
            text(f'''
                INSERT INTO "{schema}".alert_rules
                  (id, device_id, group_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails)
                VALUES (:id, :did, :gid, :sk, :cond, :thr, :tm, :cc, :ds, :sev, :emails)
            ''').bindparams(bindparam("emails", type_=ARRAY(SaString))),
            {
                "id": rule_id, "did": body.device_id, "gid": body.group_id, "sk": body.sensor_key,
                "cond": body.condition, "thr": body.threshold, "tm": body.trigger_mode,
                "cc": body.consecutive_count, "ds": body.duration_sec,
                "sev": body.severity, "emails": list(body.notify_emails),
            }
        )
        db.commit()
    return {"id": rule_id, **body.model_dump()}
```

`update_alert_rule`（tenant_portal版）を変更:

```python
@router.patch("/me/alert-rules/{rule_id}")
def update_alert_rule(rule_id: str, body: AlertRuleUpdate, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No fields to update")
    _ALLOWED_ALERT_COLS = {"sensor_key", "condition", "threshold", "trigger_mode",
                           "consecutive_count", "duration_sec", "severity", "device_id", "group_id"}
    with SessionLocal() as db:
        row = db.execute(text(f'''
            SELECT id, device_id, group_id FROM "{schema}".alert_rules WHERE id = :rid AND is_active = TRUE
        '''), {"rid": rule_id}).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
        final_device_id = updates.get("device_id", row.device_id)
        final_group_id = updates.get("group_id", row.group_id)
        if final_device_id and final_group_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="device_id and group_id are mutually exclusive")
        set_clauses = ", ".join(
            f"{col} = :{col}" for col in updates if col != "notify_emails" and col in _ALLOWED_ALERT_COLS
        )
        params = {"rid": rule_id, **{k: v for k, v in updates.items() if k != "notify_emails"}}
        if "notify_emails" in updates:
            set_clauses = (set_clauses + ", notify_emails = :notify_emails").lstrip(", ")
            stmt = text(f'UPDATE "{schema}".alert_rules SET {set_clauses} WHERE id = :rid'
                        ).bindparams(bindparam("notify_emails", type_=ARRAY(SaString)))
            params["notify_emails"] = updates["notify_emails"]
        else:
            stmt = text(f'UPDATE "{schema}".alert_rules SET {set_clauses} WHERE id = :rid')
        db.execute(stmt, params)
        db.commit()
        updated = db.execute(text(f'''
            SELECT id, device_id, group_id, sensor_key, condition, threshold, trigger_mode,
                   consecutive_count, duration_sec, severity, notify_emails, is_active
            FROM "{schema}".alert_rules WHERE id = :rid
        '''), {"rid": rule_id}).fetchone()
    return {
        "id": str(updated.id), "device_id": updated.device_id,
        "group_id": str(updated.group_id) if updated.group_id else None,
        "sensor_key": updated.sensor_key, "condition": updated.condition,
        "threshold": float(updated.threshold) if updated.threshold is not None else None,
        "trigger_mode": updated.trigger_mode, "consecutive_count": updated.consecutive_count,
        "duration_sec": updated.duration_sec, "severity": updated.severity,
        "notify_emails": list(updated.notify_emails) if updated.notify_emails else [],
        "is_active": updated.is_active,
    }
```

`list_alert_rules`（tenant_portal版）を変更:

```python
@router.get("/me/alert-rules")
def list_alert_rules(payload: dict = Depends(_require_tenant)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    with SessionLocal() as db:
        rows = db.execute(text(f'''
            SELECT r.id, r.device_id, r.group_id, r.sensor_key, r.condition, r.threshold, r.trigger_mode,
                   r.consecutive_count, r.duration_sec, r.severity, r.notify_emails, r.is_active,
                   MAX(e.triggered_at) AS last_triggered_at
            FROM "{schema}".alert_rules r
            LEFT JOIN "{schema}".alert_events e ON e.rule_id = r.id
            WHERE r.is_active = TRUE
            GROUP BY r.id
        ''')).fetchall()
    return [
        {
            "id": str(r.id),
            "device_id": r.device_id,
            "group_id": str(r.group_id) if r.group_id else None,
            "sensor_key": r.sensor_key,
            "condition": r.condition,
            "threshold": float(r.threshold) if r.threshold is not None else None,
            "trigger_mode": r.trigger_mode,
            "consecutive_count": r.consecutive_count,
            "duration_sec": r.duration_sec,
            "severity": r.severity,
            "notify_emails": list(r.notify_emails) if r.notify_emails else [],
            "is_active": r.is_active,
            "last_triggered_at": r.last_triggered_at.isoformat() if r.last_triggered_at else None,
        }
        for r in rows
    ]
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `cd core-api && python -m pytest tests/test_alert_rules_group.py -v`
Expected: 全テストPASS

- [ ] **Step 6: コミット**

```bash
git add core-api/app/routers/alert_rules.py core-api/app/routers/tenant_portal.py core-api/tests/test_alert_rules_group.py
git commit -m "feat(device-groups): アラートルールのグループ対象化と排他バリデーションを追加"
```

---

### Task 5: OTA一括送信（グループ配信）

**Files:**
- Modify: `core-api/app/services/device_groups.py`（既に `list_group_device_ids` を実装済み — Task 2で追加済み。このタスクでは変更なし、確認のみ）
- Modify: `core-api/app/routers/firmware.py`
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_device_groups_ota.py`

**Interfaces:**
- Consumes: `app.services.device_groups.list_group_device_ids(schema, group_id) -> list[str]`（Task 2）、`app.services.emqx_publisher.publish_ota_command`、`app.services.minio_client.create_firmware_download_token`
- Produces: `POST /tenants/{tenant_id}/groups/{group_id}/ota` と `POST /tenant-portal/me/groups/{group_id}/ota`（body `{"firmware_id": uuid}`）。レスポンス `{"firmware_id": str, "group_id": str, "results": [{"device_id": str, "status": "dispatched"|"failed", "error"?: str}]}`。1台失敗しても他デバイスへの送信は継続する。

- [ ] **Step 1: 失敗するテストを書く**

Create `core-api/tests/test_device_groups_ota.py`:

```python
from unittest.mock import patch, MagicMock
from app.main import app
from fastapi.testclient import TestClient
from app.services.auth import create_access_token

client = TestClient(app)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
FIRMWARE_ID = "22222222-2222-2222-2222-222222222222"
GROUP_ID = "g1"


def _platform_token():
    return create_access_token({"sub": "admin-id", "email": "admin@iot.local", "type": "platform"})


def _tenant_token(role: str = "operator"):
    return create_access_token({
        "sub": "user-id", "email": "user@test.com", "type": "tenant",
        "tenant_id": TENANT_ID, "role": role,
    })


def _firmware_row():
    return MagicMock(minio_key="key", version="1.0.0", checksum="sha256:abc", file_size=100)


def test_platform_group_ota_partial_failure():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.firmware.SessionLocal") as mock_sl, \
         patch("app.routers.firmware.publish_ota_command") as mock_publish, \
         patch("app.routers.firmware.create_firmware_download_token", return_value="tok"):
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = MagicMock(id=GROUP_ID)
        svc_conn.execute.return_value.fetchall.return_value = [
            MagicMock(device_id="dev-1"), MagicMock(device_id="dev-2"),
        ]
        mock_svc_engine.connect.return_value = svc_conn

        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.execute.return_value.fetchone.return_value = _firmware_row()
        mock_publish.side_effect = [None, Exception("mqtt down")]

        resp = client.post(
            f"/tenants/{TENANT_ID}/groups/{GROUP_ID}/ota",
            json={"firmware_id": FIRMWARE_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert results[0] == {"device_id": "dev-1", "status": "dispatched"}
    assert results[1]["device_id"] == "dev-2"
    assert results[1]["status"] == "failed"


def test_platform_group_ota_group_not_found():
    with patch("app.services.device_groups.engine") as mock_svc_engine:
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = None
        mock_svc_engine.connect.return_value = svc_conn
        resp = client.post(
            f"/tenants/{TENANT_ID}/groups/{GROUP_ID}/ota",
            json={"firmware_id": FIRMWARE_ID},
            headers={"Authorization": f"Bearer {_platform_token()}"},
        )
    assert resp.status_code == 404


def test_portal_group_ota_requires_operator_or_admin():
    resp = client.post(
        f"/tenant-portal/me/groups/{GROUP_ID}/ota",
        json={"firmware_id": FIRMWARE_ID},
        cookies={"iot_token": _tenant_token("viewer")},
    )
    assert resp.status_code == 403


def test_portal_group_ota_all_succeed():
    with patch("app.services.device_groups.engine") as mock_svc_engine, \
         patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.routers.tenant_portal.publish_ota_command") as mock_publish, \
         patch("app.routers.tenant_portal.create_firmware_download_token", return_value="tok"):
        svc_conn = MagicMock()
        svc_conn.__enter__ = lambda s: svc_conn
        svc_conn.__exit__ = MagicMock(return_value=False)
        svc_conn.execute.return_value.fetchone.return_value = MagicMock(id=GROUP_ID)
        svc_conn.execute.return_value.fetchall.return_value = [MagicMock(device_id="dev-1")]
        mock_svc_engine.connect.return_value = svc_conn

        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.execute.return_value.fetchone.return_value = _firmware_row()

        resp = client.post(
            f"/tenant-portal/me/groups/{GROUP_ID}/ota",
            json={"firmware_id": FIRMWARE_ID},
            cookies={"iot_token": _tenant_token("operator")},
        )
    assert resp.status_code == 200
    assert resp.json()["results"] == [{"device_id": "dev-1", "status": "dispatched"}]
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_device_groups_ota.py -v`
Expected: `404 Not Found`（エンドポイント未定義）で全件FAIL

- [ ] **Step 3: `firmware.py` にグループ一括OTAを追加する**

import部分に追加:

```python
from app.services.device_groups import GroupNotFoundError, list_group_device_ids
```

`OtaDispatchBody` の後に追加:

```python
class GroupOtaDispatchBody(BaseModel):
    firmware_id: str
```

`dispatch_ota_command` の後に追加:

```python
@router.post("/tenants/{tenant_id}/groups/{group_id}/ota")
def dispatch_ota_to_group(
    tenant_id: str,
    group_id: str,
    body: GroupOtaDispatchBody,
    payload: dict = Depends(_require_platform),
):
    _validate_uuid(tenant_id, "tenant_id")
    firmware_id = _validate_uuid(body.firmware_id, "firmware_id")
    tenant_name, schema_suffix = _validate_tenant(tenant_id)
    schema = f"tenant_{schema_suffix}"
    try:
        device_ids = list_group_device_ids(schema, group_id)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    with SessionLocal() as db:
        row = db.execute(text(f'''
            SELECT minio_key, version, checksum, file_size
            FROM "{schema}".firmware_releases
            WHERE id = :id AND is_active = TRUE
        '''), {"id": firmware_id}).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware not found or inactive")

        results = []
        for device_id in device_ids:
            try:
                token = create_firmware_download_token(firmware_id, tenant_id, row.minio_key)
                download_url = f"https://{settings.platform_domain}/api/firmware-download?token={token}"
                publish_ota_command(tenant_id, device_id, {
                    "firmware_id": firmware_id,
                    "version": row.version,
                    "download_url": download_url,
                    "checksum": row.checksum,
                    "file_size": row.file_size,
                })
                db.execute(text(f'''
                    INSERT INTO "{schema}".ota_events (firmware_id, device_id)
                    VALUES (:firmware_id, :device_id)
                '''), {"firmware_id": firmware_id, "device_id": device_id})
                write_audit_log(db, "platform", payload["sub"], payload["email"],
                                "ota_send", tenant_id=tenant_id,
                                resource_type="device", resource_id=device_id,
                                detail={"firmware_id": firmware_id, "version": row.version, "group_id": group_id})
                results.append({"device_id": device_id, "status": "dispatched"})
            except Exception as e:
                results.append({"device_id": device_id, "status": "failed", "error": str(e)})
        db.commit()

    return {"firmware_id": firmware_id, "group_id": group_id, "results": results}
```

- [ ] **Step 4: `tenant_portal.py` にグループ一括OTAを追加する**

import部分に追加:

```python
from app.services.device_groups import GroupNotFoundError, list_group_device_ids
```

`dispatch_ota`（`/me/devices/{device_id}/ota`）の後に追加:

```python
class _GroupOtaDispatchBody(BaseModel):
    firmware_id: str


@router.post("/me/groups/{group_id}/ota")
def dispatch_ota_to_group(group_id: str, body: _GroupOtaDispatchBody, payload: dict = Depends(_require_admin_or_operator)):
    tenant_id = payload["tenant_id"]
    schema = _schema(tenant_id)
    firmware_id = body.firmware_id
    if not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', firmware_id.lower()):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid firmware_id")
    try:
        device_ids = list_group_device_ids(schema, group_id)
    except GroupNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")

    with SessionLocal() as db:
        row = db.execute(text(f'''
            SELECT minio_key, version, checksum, file_size
            FROM "{schema}".firmware_releases
            WHERE id = :id AND is_active = TRUE
        '''), {"id": firmware_id}).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Firmware not found or inactive")

        results = []
        for device_id in device_ids:
            try:
                token = create_firmware_download_token(firmware_id, tenant_id, row.minio_key)
                download_url = f"https://{settings.platform_domain}/api/firmware-download?token={token}"
                publish_ota_command(tenant_id, device_id, {
                    "firmware_id": firmware_id,
                    "version": row.version,
                    "download_url": download_url,
                    "checksum": row.checksum,
                    "file_size": row.file_size,
                })
                db.execute(text(f'''
                    INSERT INTO "{schema}".ota_events (firmware_id, device_id)
                    VALUES (:firmware_id, :device_id)
                '''), {"firmware_id": firmware_id, "device_id": device_id})
                write_audit_log(db, "tenant", payload["sub"], payload["email"],
                                "ota_send", tenant_id=tenant_id,
                                resource_type="device", resource_id=device_id,
                                detail={"firmware_id": firmware_id, "version": row.version, "group_id": group_id})
                results.append({"device_id": device_id, "status": "dispatched"})
            except Exception as e:
                results.append({"device_id": device_id, "status": "failed", "error": str(e)})
        db.commit()

    return {"firmware_id": firmware_id, "group_id": group_id, "results": results}
```

- [ ] **Step 5: テストを実行して成功を確認する**

Run: `cd core-api && python -m pytest tests/test_device_groups_ota.py -v`
Expected: 全テストPASS

- [ ] **Step 6: コミット**

```bash
git add core-api/app/routers/firmware.py core-api/app/routers/tenant_portal.py core-api/tests/test_device_groups_ota.py
git commit -m "feat(device-groups): グループ単位のOTA一括送信を追加"
```

---

### Task 6: ダッシュボードパネル設定のグループ別上書き

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_dashboard_panel_config.py`

**Interfaces:**
- Consumes: Task 1 の `DashboardPanelConfig.group_id` 列
- Produces: `GET/PUT /tenant-portal/dashboard/panel-configs?group_id=...`（省略時は `group_id IS NULL` = テナント全体のデフォルト設定）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_dashboard_panel_config.py` に追記:

```python
def test_get_panel_configs_filters_by_group_id():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.all.return_value = [
            _make_config("temperature", "gauge"),
        ]
        resp = client.get(
            "/tenant-portal/dashboard/panel-configs?group_id=g1",
            cookies={"iot_token": _tenant_token("viewer")},
        )
    assert resp.status_code == 200
    filter_call = mock_db.query.return_value.filter.call_args[0]
    assert any("g1" in str(cond) or getattr(cond, "right", None) is not None for cond in filter_call)


def test_put_panel_configs_with_group_id_does_not_sync_grafana():
    tenant = _make_tenant()
    with patch("app.routers.tenant_portal.SessionLocal") as mock_sl, \
         patch("app.services.grafana.sync_tenant_dashboard_with_configs") as mock_sync:
        mock_db = mock_sl.return_value.__enter__.return_value
        mock_db.query.return_value.filter.return_value.first.return_value = tenant
        resp = client.put(
            "/tenant-portal/dashboard/panel-configs?group_id=g1",
            json=[{"sensor_key": "temperature", "panel_type": "gauge"}],
            cookies={"iot_token": _tenant_token("admin")},
        )
    assert resp.status_code == 204
    mock_sync.assert_not_called()
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_dashboard_panel_config.py -v`
Expected: `group_id` クエリパラメータが存在せずFAIL（未知パラメータは無視されるため実際にはフィルタ条件不足で `test_get_panel_configs_filters_by_group_id` がFAIL、`test_put_panel_configs_with_group_id_does_not_sync_grafana` は現状 `mock_sync` が呼ばれてしまいFAIL）

- [ ] **Step 3: `tenant_portal.py` のダッシュボードパネル設定エンドポイントを更新する**

`get_panel_configs` を変更:

```python
@router.get("/dashboard/panel-configs")
def get_panel_configs(group_id: Optional[str] = Query(default=None), payload: dict = Depends(_require_tenant)):
    from app.models.public import DashboardPanelConfig
    tenant_id = payload["tenant_id"]
    with SessionLocal() as db:
        rows = db.query(DashboardPanelConfig).filter(
            DashboardPanelConfig.tenant_id == tenant_id,
            DashboardPanelConfig.group_id == group_id,
        ).all()
    return [{"sensor_key": r.sensor_key, "panel_type": r.panel_type} for r in rows]
```

`put_panel_configs` を変更:

```python
@router.put("/dashboard/panel-configs", status_code=204)
def put_panel_configs(
    items: list[PanelConfigItem],
    group_id: Optional[str] = Query(default=None),
    payload: dict = Depends(_require_admin_or_operator),
):
    from app.models.public import DashboardPanelConfig, Tenant
    from app.services.grafana import sync_tenant_dashboard_with_configs
    tenant_id = payload["tenant_id"]

    sensor_keys = [item.sensor_key for item in items]
    if len(sensor_keys) != len(set(sensor_keys)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Duplicate sensor_key values")

    with SessionLocal() as db:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

        grafana_org_id = tenant.grafana_org_id
        tenant_name = tenant.name

        db.query(DashboardPanelConfig).filter(
            DashboardPanelConfig.tenant_id == tenant_id,
            DashboardPanelConfig.group_id == group_id,
        ).delete()
        for item in items:
            db.add(DashboardPanelConfig(
                tenant_id=tenant_id,
                group_id=group_id,
                sensor_key=item.sensor_key,
                panel_type=item.panel_type.value,
            ))
        db.commit()

    # グループ別設定は Grafana ダッシュボード同期の対象外（テナント全体のデフォルトのみ同期）
    if grafana_org_id and group_id is None:
        configs = [{"sensor_key": i.sensor_key, "panel_type": i.panel_type.value} for i in items]
        try:
            sync_tenant_dashboard_with_configs(int(grafana_org_id), tenant_name, configs)
        except Exception as e:
            print(f"[panel_configs] Grafana sync failed: {e}")
```

- [ ] **Step 4: テストを実行して成功を確認する**

Run: `cd core-api && python -m pytest tests/test_dashboard_panel_config.py -v`
Expected: 全テストPASS

- [ ] **Step 5: コミット**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_dashboard_panel_config.py
git commit -m "feat(device-groups): ダッシュボードパネル設定のグループ別上書きに対応"
```

---

### Task 7: 全体テスト実行と後片付け

**Files:** なし（検証のみ）

- [ ] **Step 1: バックエンドの全テストスイートを実行する**

Run: `cd core-api && python -m pytest tests/ -v`
Expected: 全テストPASS（既存テストも含め、`group_id` 追加によるレスポンス形状変化で壊れているものがないか最終確認）

- [ ] **Step 2: 実装がspecの「含む」項目を全てカバーしているか確認する**

`docs/superpowers/specs/2026-09-08-device-groups-design.md` の「スコープ」節の「含む」6項目（グループCRUD／デバイス割当／アラートルール対象化／OTA一括配信／ダッシュボード上書き／監査ログ記録）に対応するタスクが全て完了していることをチェックリストと突き合わせて確認する。

- [ ] **Step 3: このプランをコミットする（まだの場合）**

```bash
git add docs/superpowers/plans/2026-09-08-device-groups-backend.md
git commit -m "docs: デバイスグループ管理機能（バックエンド）の実装プランを追加"
```
