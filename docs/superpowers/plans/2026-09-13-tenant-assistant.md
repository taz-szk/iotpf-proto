# テナント管理者向けAIアシスタント展開 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PF管理者限定のAIアシスタント機能（Phase 1）を、テナント管理者（admin/operatorロール）にも展開する。テナント自己サービスAPIをラップした専用ツールセットと、常時表示のフローティングウィジェットUIを追加する。

**Architecture:** `app/services/rag.py`の`answer_question`/`execute_pending_action`に`tools`/`payload`パラメータを追加し、既存ロジックをPF管理者・テナント両フローで共有する。テナント向けツールは`app/services/rag_tools/tenant/`に新設し、`tenant_portal.py`の自己サービスAPIを実ペイロードでラップする。APIは`tenant_portal.py`に`/me/assistant/chat`・`/me/assistant/confirm-action`を追加。UIは`admin-ui/tenant-portal.html`に常時表示ウィジェット（小窓⇄全画面）を追加する。

**Tech Stack:** FastAPI, SQLAlchemy, Alpine.js, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-09-13-tenant-assistant-design.md`

## Global Constraints

- テナント越境防止: ハンドラに渡す`tenant_id`/`payload`は常にURLパス・認証済みJWT由来の値のみ。モデルが`tool_args`に混入させた`tenant_id`/`payload`は`_sanitize_tool_args`で除去する。
- `TENANT_TOOLS`に全テナント一覧を返す`tenant_list`相当のツールは含めない（テナント越境情報漏洩防止）。
- テナント向け操作代行ツールは、PF管理者向けAPI（`provisioning_tokens.py`・`alert_rules.py`）ではなく、テナント自己サービスAPI（`tenant_portal.py`）をラップする。
- ダッシュボード設定ツールは、既存の`put_panel_configs`が「渡された`items`で全置換」するセマンティクスを持つため、`get_panel_configs`で既存設定を取得しマージしてから渡す方式にする（PF管理者向け実装で修正済みの同じ罠を再導入しない）。
- プロビジョニングトークン発行ツールは、引数省略時に`TokenCreate`へ`None`を明示的に渡さない（番兵値の踏み抜き防止、PF管理者向けC-2と同じ罠を再導入しない）。
- PF管理者向けの既存ファイル（`app/routers/rag.py`・`app/services/rag_tools/provisioning.py`・`dashboard.py`・`alerts.py`・`general.py`・`platform-ui/tenant.html`）は変更しない。
- 新規ドキュメントインデックスは作らない。PF管理者向けと同じ`doc_chunks`をそのまま共有する。
- 再インデックスエンドポイントはテナント向けに追加しない。

---

### Task 1: `app/services/rag.py`の`tools`/`payload`パラメータ拡張

**Files:**
- Modify: `core-api/app/services/rag.py`
- Test: `core-api/tests/test_rag.py`

**Interfaces:**
- Consumes: 既存の`TOOLS`（`app/services/rag_tools`）、`AgentPendingAction`、`get_assistant_settings`、`chat`/`embed`（変更なし）
- Produces: `answer_question(db, tenant_id, message, requested_by, tools=None, payload=None) -> dict`、`execute_pending_action(db, tenant_id, pending_action_id, tools=None, payload=None)`。Task 3がテナント向け呼び出しで`tools=TENANT_TOOLS, payload=<認証済みペイロード>`を渡す。

- [ ] **Step 1: 失敗するテストを書く（`_sanitize_tool_args`の`payload`除去）**

`core-api/tests/test_rag.py`の`from app.services.rag import answer_question, execute_pending_action`の下に追記:

```python
from app.services.rag import _sanitize_tool_args


def test_sanitize_tool_args_removes_payload_key():
    result = _sanitize_tool_args({"sensor_key": "temperature", "tenant_id": "other", "payload": {"role": "admin"}})
    assert result == {"sensor_key": "temperature"}
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_rag.py::test_sanitize_tool_args_removes_payload_key -v`
Expected: FAIL（`payload`キーがまだ残っている、`assert {"sensor_key": "temperature", "payload": {"role": "admin"}} == {"sensor_key": "temperature"}`で失敗）

- [ ] **Step 3: `_sanitize_tool_args`を修正する**

`core-api/app/services/rag.py`の以下:
```python
def _sanitize_tool_args(tool_args: dict) -> dict:
    """モデルが返したtool_argsから'tenant_id'キーを除去する。
    ハンドラのtenant_idは常に呼び出し元のURLパス由来の値のみを使い、
    モデルが返した値は信用しない（agent_pending_actionsへの永続化前にも適用する）。"""
    return {k: v for k, v in tool_args.items() if k != "tenant_id"}
```
を以下に置き換え:
```python
def _sanitize_tool_args(tool_args: dict) -> dict:
    """モデルが返したtool_argsから'tenant_id'/'payload'キーを除去する。
    ハンドラのtenant_id/payloadは常に呼び出し元の認証済みコンテキスト由来の値のみを使い、
    モデルが返した値は信用しない（agent_pending_actionsへの永続化前にも適用する）。"""
    return {k: v for k, v in tool_args.items() if k not in ("tenant_id", "payload")}
```

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_rag.py::test_sanitize_tool_args_removes_payload_key -v`
Expected: PASS

- [ ] **Step 5: 失敗するテストを書く（`answer_question`の`tools`/`payload`パラメータ）**

`core-api/tests/test_rag.py`の`test_execute_pending_action_returns_none_and_deletes_when_expired`の直前に追記:

```python
def test_answer_question_uses_custom_tools_registry_when_provided():
    """toolsパラメータを渡すと、そのレジストリからツールが解決される（グローバルTOOLSは使われない）。"""
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []

    tool_call = {"id": "call-1", "function": {"name": "tenant_only_tool", "arguments": "{}"}}
    responses = [
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "assistant", "content": "回答です", "tool_calls": None},
    ]
    custom_tool = MagicMock(read_only=True)
    custom_tool.name = "tenant_only_tool"
    custom_tool.handler = MagicMock(return_value={"ok": True})

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", side_effect=responses):
        result = answer_question(
            mock_db, tenant_id="tenant-1", message="質問", requested_by="admin@example.com",
            tools=[custom_tool],
        )

    assert result["answer"] == "回答です"
    custom_tool.handler.assert_called_once_with(tenant_id="tenant-1")


def test_answer_question_passes_payload_to_handler_when_provided():
    """payloadパラメータを渡すと、read_onlyツールハンドラにそのままpayloadキーワードで渡される。"""
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []

    tool_call = {"id": "call-1", "function": {"name": "tenant_only_tool", "arguments": "{}"}}
    responses = [
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "assistant", "content": "回答です", "tool_calls": None},
    ]
    custom_tool = MagicMock(read_only=True)
    custom_tool.name = "tenant_only_tool"
    custom_tool.handler = MagicMock(return_value={"ok": True})
    fake_payload = {"sub": "user-1", "email": "admin@tenant.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", side_effect=responses):
        answer_question(
            mock_db, tenant_id="tenant-1", message="質問", requested_by="admin@tenant.example",
            tools=[custom_tool], payload=fake_payload,
        )

    custom_tool.handler.assert_called_once_with(tenant_id="tenant-1", payload=fake_payload)


def test_answer_question_omits_payload_kwarg_when_not_provided():
    """payload未指定(PF管理者フロー)の場合、ハンドラにpayloadキーワード自体を渡さない。"""
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []

    tool_call = {"id": "call-1", "function": {"name": "tenant_stats_get", "arguments": "{}"}}
    responses = [
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "assistant", "content": "回答です", "tool_calls": None},
    ]

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", side_effect=responses), \
         patch("app.services.rag.TOOLS") as mock_tools:
        read_tool = MagicMock(read_only=True)
        read_tool.name = "tenant_stats_get"
        read_tool.handler = MagicMock(return_value={"device_count": 5})
        mock_tools.__iter__.return_value = iter([read_tool])

        answer_question(mock_db, tenant_id="tenant-1", message="統計は？", requested_by="admin@example.com")

    read_tool.handler.assert_called_once_with(tenant_id="tenant-1")


def test_execute_pending_action_passes_payload_and_uses_tenant_actor_for_audit():
    """payload指定時、ハンドラにpayloadを渡し、監査ログもactor_type='tenant'・実sub/emailで記録する。"""
    pending = MagicMock()
    pending.id = "pending-1"
    pending.tenant_id = "tenant-1"
    pending.tool_name = "tenant_alert_rule_create"
    pending.tool_args = {"sensor_key": "temperature", "condition": "above"}
    pending.requested_by = "admin@tenant.example"
    pending.expires_at.__gt__ = lambda self, other: True

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = pending
    fake_payload = {"sub": "11111111-1111-1111-1111-111111111111", "email": "admin@tenant.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}

    tenant_tool = MagicMock()
    tenant_tool.name = "tenant_alert_rule_create"
    tenant_tool.handler = MagicMock(return_value={"id": "rule-1"})

    with patch("app.services.rag.write_audit_log") as mock_audit:
        result = execute_pending_action(
            mock_db, tenant_id="tenant-1", pending_action_id="pending-1",
            tools=[tenant_tool], payload=fake_payload,
        )

    tenant_tool.handler.assert_called_once_with(
        tenant_id="tenant-1", payload=fake_payload, sensor_key="temperature", condition="above",
    )
    assert result == {"id": "rule-1"}
    audit_call = mock_audit.call_args
    assert audit_call.args[1] == "tenant"
    assert audit_call.args[2] == "11111111-1111-1111-1111-111111111111"
    assert audit_call.args[3] == "admin@tenant.example"
```

- [ ] **Step 6: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_rag.py -k "custom_tools_registry or passes_payload or omits_payload or tenant_actor_for_audit" -v`
Expected: FAIL（`answer_question()`/`execute_pending_action()`が`tools`/`payload`キーワード引数を受け付けない`TypeError`）

- [ ] **Step 7: `answer_question`を修正する**

`core-api/app/services/rag.py`の以下:
```python
def answer_question(db: Session, tenant_id: str, message: str, requested_by: str) -> dict:
    """質問に回答する。読み取り専用ツールは即実行し、アクション実行ツールは
    pending_actionとして保存し確認待ちにする。
    戻り値: {"answer": str, "sources": list[dict], "pending_action": dict | None}"""
    ollama_settings = get_assistant_settings(db)
    ollama_url = ollama_settings.ollama_url
    chat_model = ollama_settings.ollama_chat_model
    embed_model = ollama_settings.ollama_embed_model

    tools = list(TOOLS)
```
を以下に置き換え:
```python
def answer_question(
    db: Session, tenant_id: str, message: str, requested_by: str,
    tools: list | None = None, payload: dict | None = None,
) -> dict:
    """質問に回答する。読み取り専用ツールは即実行し、アクション実行ツールは
    pending_actionとして保存し確認待ちにする。
    tools省略時はPF管理者向けのグローバルTOOLSを使う。payloadはテナント向け呼び出し時のみ
    認証済みJWTペイロードを渡す（PF管理者向けはNoneのまま、ハンドラにpayloadキーワード自体を渡さない）。
    戻り値: {"answer": str, "sources": list[dict], "pending_action": dict | None}"""
    ollama_settings = get_assistant_settings(db)
    ollama_url = ollama_settings.ollama_url
    chat_model = ollama_settings.ollama_chat_model
    embed_model = ollama_settings.ollama_embed_model

    tools = list(tools if tools is not None else TOOLS)
```

同ファイルの以下:
```python
        if tool.read_only:
            try:
                result = tool.handler(tenant_id=tenant_id, **tool_args)
            except Exception as e:
```
を以下に置き換え:
```python
        if tool.read_only:
            handler_kwargs = {"tenant_id": tenant_id, **tool_args}
            if payload is not None:
                handler_kwargs["payload"] = payload
            try:
                result = tool.handler(**handler_kwargs)
            except Exception as e:
```

- [ ] **Step 8: `execute_pending_action`を修正する**

同ファイルの以下:
```python
def execute_pending_action(db: Session, tenant_id: str, pending_action_id: str):
    """未確認アクションを実行する。見つからない/期限切れならNoneを返す。
    ツールがレジストリから削除/リネームされていて実行不能な場合はレコードを削除しValueErrorを送出する。"""
    pending = db.query(AgentPendingAction).filter(
        AgentPendingAction.id == pending_action_id,
        AgentPendingAction.tenant_id == tenant_id,
    ).first()
    if pending is None:
        return None
    if not (pending.expires_at > datetime.now(timezone.utc)):
        db.delete(pending)
        db.commit()
        return None

    tools = list(TOOLS)
    tool = _find_tool(tools, pending.tool_name)
    if tool is None:
        db.delete(pending)
        db.commit()
        raise ValueError(f"Tool '{pending.tool_name}' is no longer registered")

    tool_args = _sanitize_tool_args(pending.tool_args)
    result = tool.handler(tenant_id=tenant_id, **tool_args)

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
を以下に置き換え:
```python
def execute_pending_action(
    db: Session, tenant_id: str, pending_action_id: str,
    tools: list | None = None, payload: dict | None = None,
):
    """未確認アクションを実行する。見つからない/期限切れならNoneを返す。
    ツールがレジストリから削除/リネームされていて実行不能な場合はレコードを削除しValueErrorを送出する。
    payload指定時（テナント向け）は監査ログをactor_type='tenant'・実sub/emailで記録する。"""
    pending = db.query(AgentPendingAction).filter(
        AgentPendingAction.id == pending_action_id,
        AgentPendingAction.tenant_id == tenant_id,
    ).first()
    if pending is None:
        return None
    if not (pending.expires_at > datetime.now(timezone.utc)):
        db.delete(pending)
        db.commit()
        return None

    tools = list(tools if tools is not None else TOOLS)
    tool = _find_tool(tools, pending.tool_name)
    if tool is None:
        db.delete(pending)
        db.commit()
        raise ValueError(f"Tool '{pending.tool_name}' is no longer registered")

    tool_args = _sanitize_tool_args(pending.tool_args)
    handler_kwargs = {"tenant_id": tenant_id, **tool_args}
    if payload is not None:
        handler_kwargs["payload"] = payload
    result = tool.handler(**handler_kwargs)

    if payload is not None:
        actor_type = "tenant"
        actor_id = payload.get("sub", "00000000-0000-0000-0000-000000000000")
        actor_email = payload.get("email", pending.requested_by)
    else:
        actor_type = "platform"
        actor_id = "00000000-0000-0000-0000-000000000000"
        actor_email = pending.requested_by

    write_audit_log(
        db, actor_type, actor_id, actor_email,
        f"ai_assistant_{pending.tool_name}",
        tenant_id=tenant_id, resource_type="ai_assistant_action",
        detail={"via": "ai_assistant", "confirmed_by": pending.requested_by, "tool_args": pending.tool_args},
    )
    db.delete(pending)
    db.commit()
    return result
```

- [ ] **Step 9: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_rag.py -v`
Expected: PASS（既存13件＋新規4件＋Step1の1件＝合計18件全てPASS。既存13件は無変更で通ること）

- [ ] **Step 10: コミット**

```bash
git add core-api/app/services/rag.py core-api/tests/test_rag.py
git commit -m "feat: rag.pyにtools/payloadパラメータを追加しテナント向け呼び出しに対応"
```

---

### Task 2: テナント向けツールレジストリ＋3ドメイン

**Files:**
- Create: `core-api/app/services/rag_tools/tenant/__init__.py`
- Create: `core-api/app/services/rag_tools/tenant/provisioning.py`
- Create: `core-api/app/services/rag_tools/tenant/dashboard.py`
- Create: `core-api/app/services/rag_tools/tenant/alerts.py`
- Test: `core-api/tests/test_rag_tools_tenant.py`

**Interfaces:**
- Consumes: `AgentTool`（`app/services/rag_tools`）、`create_token`/`list_tokens`/`get_panel_configs`/`put_panel_configs`/`PanelConfigItem`/`create_alert_rule`/`list_alert_rules`/`AlertRuleCreate`（すべて`app/routers/tenant_portal.py`の既存関数、変更しない）
- Produces: `TENANT_TOOLS: list[AgentTool]`（`app/services/rag_tools/tenant/__init__.py`。Task 3が`answer_question(..., tools=TENANT_TOOLS, ...)`として使う）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_rag_tools_tenant.py`を新規作成:

```python
from unittest.mock import MagicMock, patch

from app.services.rag_tools.tenant import TENANT_TOOLS


def test_all_tenant_tools_have_required_fields():
    for tool in TENANT_TOOLS:
        assert tool.name
        assert tool.description
        assert isinstance(tool.input_schema, dict)
        assert isinstance(tool.read_only, bool)
        assert callable(tool.handler)


def test_tenant_tool_names_are_unique_and_prefixed():
    names = [t.name for t in TENANT_TOOLS]
    assert len(names) == len(set(names))
    assert all(n.startswith("tenant_") for n in names)


def test_tenant_list_tool_is_not_present():
    """全テナント一覧に相当するツールは越境漏洩リスクのため絶対に含めない。"""
    names = [t.name for t in TENANT_TOOLS]
    assert not any("tenant_list" == n or n.endswith("_tenant_list") for n in names)


def test_action_tools_are_marked_not_read_only():
    action_names = {"tenant_provisioning_token_issue", "tenant_dashboard_panel_config_set", "tenant_alert_rule_create"}
    for tool in TENANT_TOOLS:
        if tool.name in action_names:
            assert tool.read_only is False


def test_read_only_tools_are_marked_read_only():
    read_only_names = {"tenant_provisioning_token_list", "tenant_dashboard_panel_config_list", "tenant_alert_rule_list"}
    for tool in TENANT_TOOLS:
        if tool.name in read_only_names:
            assert tool.read_only is True


def test_provisioning_token_issue_wraps_tenant_self_service_api():
    from app.services.rag_tools.tenant.provisioning import issue_provisioning_token

    fake_payload = {"sub": "u1", "email": "a@t.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}
    with patch("app.services.rag_tools.tenant.provisioning.create_token") as mock_create:
        mock_create.return_value = {"id": "token-1"}
        result = issue_provisioning_token(tenant_id="tenant-1", payload=fake_payload)

    mock_create.assert_called_once()
    call_args = mock_create.call_args
    assert call_args.kwargs["payload"] == fake_payload
    assert call_args.kwargs["body"].max_devices == 100
    assert result == {"id": "token-1"}


def test_provisioning_token_issue_omits_none_fields_to_avoid_sentinel():
    """max_devices/expires_days省略時にNoneを明示的にTokenCreateへ渡さない(番兵値踏み抜き防止)。"""
    from app.services.rag_tools.tenant.provisioning import issue_provisioning_token

    fake_payload = {"sub": "u1", "email": "a@t.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}
    with patch("app.services.rag_tools.tenant.provisioning.create_token") as mock_create:
        mock_create.return_value = {"id": "token-1"}
        issue_provisioning_token(tenant_id="tenant-1", payload=fake_payload, expires_days=30)

    body = mock_create.call_args.kwargs["body"]
    assert body.max_devices == 100  # TokenCreate自身の既定値
    assert body.expires_days == 30


def test_dashboard_panel_config_set_merges_with_existing_configs():
    from app.services.rag_tools.tenant.dashboard import set_dashboard_panel_config

    fake_payload = {"sub": "u1", "email": "a@t.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}
    with patch("app.services.rag_tools.tenant.dashboard.get_panel_configs", return_value=[{"sensor_key": "humidity", "panel_type": "gauge"}]), \
         patch("app.services.rag_tools.tenant.dashboard.put_panel_configs") as mock_put:
        set_dashboard_panel_config(tenant_id="tenant-1", payload=fake_payload, sensor_key="temperature", panel_type="timeseries")

    mock_put.assert_called_once()
    call_args = mock_put.call_args
    assert call_args.kwargs["payload"] == fake_payload
    items_by_key = {i.sensor_key: i.panel_type for i in call_args.kwargs["items"]}
    assert items_by_key["temperature"] == "timeseries"
    assert items_by_key["humidity"] == "gauge"


def test_alert_rule_create_wraps_tenant_self_service_api():
    from app.services.rag_tools.tenant.alerts import create_alert_rule_tool

    fake_payload = {"sub": "u1", "email": "a@t.example", "role": "admin", "tenant_id": "tenant-1", "type": "tenant"}
    with patch("app.services.rag_tools.tenant.alerts.create_alert_rule") as mock_create:
        mock_create.return_value = {"id": "rule-1"}
        result = create_alert_rule_tool(tenant_id="tenant-1", payload=fake_payload, sensor_key="temperature", condition="above", threshold=80.0)

    call_args = mock_create.call_args
    assert call_args.kwargs["payload"] == fake_payload
    assert call_args.kwargs["body"].sensor_key == "temperature"
    assert call_args.kwargs["body"].condition == "above"
    assert call_args.kwargs["body"].threshold == 80.0
    assert result == {"id": "rule-1"}
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_rag_tools_tenant.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'app.services.rag_tools.tenant'`）

- [ ] **Step 3: `rag_tools/tenant/__init__.py`を新規作成する**

```python
from app.services.rag_tools import AgentTool  # noqa: F401  (再エクスポートしないが型参照用)

from . import alerts, dashboard, provisioning  # noqa: E402

TENANT_TOOLS: list[AgentTool] = [
    *provisioning.TOOLS,
    *dashboard.TOOLS,
    *alerts.TOOLS,
]
# 注意: 全テナント一覧(tenant_list相当)は絶対に追加しないこと。
# テナント自身が他テナントの情報を取得できてしまう越境漏洩になる。
```

- [ ] **Step 4: `rag_tools/tenant/provisioning.py`を新規作成する**

```python
from app.routers.tenant_portal import TokenCreate, create_token, list_tokens
from app.services.rag_tools import AgentTool


def issue_provisioning_token(tenant_id: str, payload: dict, max_devices: int | None = None, expires_days: int | None = None) -> dict:
    # TokenCreateはmax_devicesが常にint必須(Noneにできない)なので番兵値の心配はないが、
    # expires_daysは省略時にNoneを明示的に渡すと無期限番兵値を踏み抜く。指定分のみ渡す。
    body_kwargs: dict = {}
    if max_devices is not None:
        body_kwargs["max_devices"] = max_devices
    if expires_days is not None:
        body_kwargs["expires_days"] = expires_days
    return create_token(body=TokenCreate(**body_kwargs), payload=payload)


def list_my_provisioning_tokens(tenant_id: str, payload: dict) -> list[dict]:
    return list_tokens(payload=payload)


TOOLS = [
    AgentTool(
        name="tenant_provisioning_token_issue",
        description="自テナントの新しいプロビジョニングトークンを発行する。デバイスをプラットフォームに登録するために必要。",
        input_schema={
            "type": "object",
            "properties": {
                "max_devices": {"type": "integer", "description": "登録可能な最大デバイス数（省略時は100）"},
                "expires_days": {"type": "integer", "description": "有効期限（日数、省略時は365日。無期限にはできません）"},
            },
        },
        read_only=False,
        handler=issue_provisioning_token,
    ),
    AgentTool(
        name="tenant_provisioning_token_list",
        description="自テナントの発行済みプロビジョニングトークン一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_my_provisioning_tokens,
    ),
]
```

- [ ] **Step 5: `rag_tools/tenant/dashboard.py`を新規作成する**

```python
from app.routers.tenant_portal import PanelConfigItem, get_panel_configs, put_panel_configs
from app.services.rag_tools import AgentTool


def set_dashboard_panel_config(tenant_id: str, payload: dict, sensor_key: str, panel_type: str) -> dict:
    existing = get_panel_configs(group_id=None, payload=payload)
    merged = {item["sensor_key"]: item["panel_type"] for item in existing}
    merged[sensor_key] = panel_type
    items = [PanelConfigItem(sensor_key=k, panel_type=v) for k, v in merged.items()]
    put_panel_configs(items=items, group_id=None, payload=payload)
    return {"sensor_key": sensor_key, "panel_type": panel_type}


def list_dashboard_panel_configs(tenant_id: str, payload: dict) -> list[dict]:
    return get_panel_configs(group_id=None, payload=payload)


TOOLS = [
    AgentTool(
        name="tenant_dashboard_panel_config_set",
        description="自テナントのダッシュボードで、指定センサーの表示形式（グラフ種別）を設定する。",
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
        name="tenant_dashboard_panel_config_list",
        description="自テナントの現在のダッシュボード表示設定一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_dashboard_panel_configs,
    ),
]
```

- [ ] **Step 6: `rag_tools/tenant/alerts.py`を新規作成する**

```python
from app.routers.tenant_portal import AlertRuleCreate, create_alert_rule, list_alert_rules
from app.services.rag_tools import AgentTool


def create_alert_rule_tool(
    tenant_id: str, payload: dict, sensor_key: str, condition: str, threshold: float | None = None,
    severity: str = "warning", notify_emails: list[str] | None = None,
) -> dict:
    return create_alert_rule(
        body=AlertRuleCreate(
            sensor_key=sensor_key, condition=condition, threshold=threshold,
            severity=severity, notify_emails=notify_emails or [],
        ),
        payload=payload,
    )


def list_my_alert_rules(tenant_id: str, payload: dict) -> list[dict]:
    return list_alert_rules(payload=payload)


TOOLS = [
    AgentTool(
        name="tenant_alert_rule_create",
        description="自テナントにアラートルールを作成する。センサー値が条件を満たしたときに通知する。",
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
        name="tenant_alert_rule_list",
        description="自テナントの現在のアラートルール一覧を取得する。",
        input_schema={"type": "object", "properties": {}},
        read_only=True,
        handler=list_my_alert_rules,
    ),
]
```

- [ ] **Step 7: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_rag_tools_tenant.py -v`
Expected: PASS（全9件）

- [ ] **Step 8: コミット**

```bash
git add core-api/app/services/rag_tools/tenant/ core-api/tests/test_rag_tools_tenant.py
git commit -m "feat: テナント向けAIアシスタントツールレジストリ(プロビジョニング/ダッシュボード/アラート)を追加"
```

---

### Task 3: テナント向けAPI（`/me/assistant/chat`・`/me/assistant/confirm-action`）

**Files:**
- Modify: `core-api/app/routers/tenant_portal.py`
- Test: `core-api/tests/test_tenant_portal_assistant.py`

**Interfaces:**
- Consumes: `answer_question`/`execute_pending_action`（Task 1で拡張済み）、`TENANT_TOOLS`（Task 2）、`is_assistant_configured`（`app/services/assistant_settings`、既存）
- Produces: `POST /tenant-portal/me/assistant/chat`, `POST /tenant-portal/me/assistant/confirm-action`（ルーターのprefixは`/tenant-portal`）

- [ ] **Step 1: 失敗するテストを書く**

`core-api/tests/test_tenant_portal_assistant.py`を新規作成:

```python
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.services.auth import create_access_token

client = TestClient(app)
TENANT_ID = "11111111-1111-1111-1111-111111111111"
PENDING_ACTION_ID = "22222222-2222-2222-2222-222222222222"


def _tenant_cookie(role="admin"):
    token = create_access_token({"sub": "user-1", "email": "admin@tenant.example", "tenant_id": TENANT_ID, "role": role, "type": "tenant"})
    return {"iot_token": token}


def _session_ctx():
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    return mock_db


def test_chat_requires_tenant_auth():
    resp = client.post("/tenant-portal/me/assistant/chat", json={"message": "質問"})
    assert resp.status_code == 401


def test_chat_returns_answer_for_admin():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.answer_question", return_value={"answer": "回答です", "sources": [], "pending_action": None}) as mock_answer:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/chat",
            json={"message": "質問"},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 200
    assert resp.json() == {"answer": "回答です", "sources": [], "pending_action": None}
    call_kwargs = mock_answer.call_args.kwargs
    assert call_kwargs["tenant_id"] == TENANT_ID
    assert call_kwargs["message"] == "質問"
    assert call_kwargs["requested_by"] == "admin@tenant.example"
    from app.services.rag_tools.tenant import TENANT_TOOLS
    assert call_kwargs["tools"] is TENANT_TOOLS
    assert call_kwargs["payload"]["tenant_id"] == TENANT_ID


def test_chat_returns_answer_for_operator():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.answer_question", return_value={"answer": "回答です", "sources": [], "pending_action": None}):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/chat",
            json={"message": "質問"},
            cookies=_tenant_cookie("operator"),
        )
    assert resp.status_code == 200


def test_chat_returns_503_when_not_configured():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=False):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/chat",
            json={"message": "質問"},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 503


def test_confirm_action_requires_tenant_auth():
    resp = client.post("/tenant-portal/me/assistant/confirm-action", json={"pending_action_id": PENDING_ACTION_ID})
    assert resp.status_code == 401


def test_confirm_action_executes_and_returns_result():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.execute_pending_action", return_value={"id": "rule-1"}) as mock_execute:
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 200
    assert resp.json() == {"result": {"id": "rule-1"}}
    call_kwargs = mock_execute.call_args.kwargs
    assert call_kwargs["tenant_id"] == TENANT_ID
    assert call_kwargs["payload"]["tenant_id"] == TENANT_ID


def test_confirm_action_returns_404_when_not_found():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.execute_pending_action", return_value=None):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 404


def test_confirm_action_returns_409_when_tool_no_longer_registered():
    with patch("app.routers.tenant_portal.SessionLocal") as mock_session, \
         patch("app.routers.tenant_portal.is_assistant_configured", return_value=True), \
         patch("app.routers.tenant_portal.execute_pending_action", side_effect=ValueError("Tool 'x' is no longer registered")):
        mock_session.return_value = _session_ctx()
        resp = client.post(
            "/tenant-portal/me/assistant/confirm-action",
            json={"pending_action_id": PENDING_ACTION_ID},
            cookies=_tenant_cookie("admin"),
        )
    assert resp.status_code == 409


def test_confirm_action_returns_422_for_invalid_pending_action_id_format():
    resp = client.post(
        "/tenant-portal/me/assistant/confirm-action",
        json={"pending_action_id": "not-a-uuid"},
        cookies=_tenant_cookie("admin"),
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: テストを実行して失敗を確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_assistant.py -v`
Expected: FAIL（404、対応するルートが無い）

- [ ] **Step 3: `tenant_portal.py`にエンドポイントを追加する**

`core-api/app/routers/tenant_portal.py`の先頭付近の既存import群に以下を追記（ファイル内の他のimport文と同じ場所にまとめる）:

```python
from app.services.assistant_settings import is_assistant_configured
from app.services.rag import answer_question, execute_pending_action
from app.services.rag_tools.tenant import TENANT_TOOLS
```

ファイルの末尾に以下を追記:

```python


class AssistantChatBody(BaseModel):
    message: str


class AssistantConfirmActionBody(BaseModel):
    pending_action_id: str


@router.post("/me/assistant/chat")
def chat_with_tenant_assistant(body: AssistantChatBody, payload: dict = Depends(_require_admin_or_operator)):
    with SessionLocal() as db:
        if not is_assistant_configured(db):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI assistant is not configured")
        return answer_question(
            db, tenant_id=payload["tenant_id"], message=body.message, requested_by=payload["email"],
            tools=TENANT_TOOLS, payload=payload,
        )


@router.post("/me/assistant/confirm-action")
def confirm_tenant_assistant_action(body: AssistantConfirmActionBody, payload: dict = Depends(_require_admin_or_operator)):
    pending_action_id = _validate_uuid(body.pending_action_id, "pending_action_id")
    with SessionLocal() as db:
        if not is_assistant_configured(db):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI assistant is not configured")
        try:
            result = execute_pending_action(
                db, tenant_id=payload["tenant_id"], pending_action_id=pending_action_id,
                tools=TENANT_TOOLS, payload=payload,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
        except TypeError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pending action not found or expired")
    return {"result": result}
```

（`BaseModel`・`Depends`・`HTTPException`・`status`・`SessionLocal`は既存ファイルで既にimport済みのはずなので重複させないこと。実装時にファイル先頭を確認すること。）

- [ ] **Step 4: テストを実行して通過を確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_assistant.py -v`
Expected: PASS（全9件）

- [ ] **Step 5: 既存のtenant_portalテストに回帰がないか確認する**

Run: `cd core-api && python -m pytest tests/test_tenant_portal_tokens.py tests/test_tenant_portal_stats.py tests/test_tenant_portal_public_access.py tests/test_tenant_portal_billing.py tests/test_tenant_portal_data_retention.py -v`
Expected: PASS（全件、既存の失敗と同じ既知のもの以外の新規失敗が無いこと）

- [ ] **Step 6: コミット**

```bash
git add core-api/app/routers/tenant_portal.py core-api/tests/test_tenant_portal_assistant.py
git commit -m "feat: テナント向けAIアシスタントのチャット・確認実行APIを追加"
```

---

### Task 4: フロントエンド（常時表示フローティングウィジェット）

**Files:**
- Modify: `admin-ui/tenant-portal.html`

このタスクに自動テストはありません（既存のUI機能と同様、手動確認）。

- [ ] **Step 1: ウィジェットのHTMLを追加する**

`admin-ui/tenant-portal.html`を開き、`<div x-data="portalApp()" x-init="init()" class="flex h-screen overflow-hidden">`のルート要素を見つける。その閉じタグ（`</div>`、ファイル末尾近く、`</body>`の直前）の直前に、以下を追記する:

```html
    <!-- AIアシスタント: 常時表示フローティングウィジェット -->
    <div x-show="assistantState !== 'collapsed'"
         :class="assistantState === 'fullscreen' ? 'fixed inset-0 z-50 p-4' : 'fixed bottom-4 right-4 z-50'"
         style="display: none;">
      <div :class="assistantState === 'fullscreen' ? 'w-full h-full' : ''"
           style="width: 360px; height: 480px; min-width: 280px; min-height: 320px; max-width: 100%; max-height: 100%; resize: both; overflow: auto;"
           :style="assistantState === 'fullscreen' ? 'width: 100%; height: 100%; resize: none;' : ''"
           class="bg-white rounded-xl shadow-2xl border border-gray-200 flex flex-col">
        <div class="flex items-center justify-between px-3 py-2 border-b border-gray-200">
          <span class="text-sm font-semibold text-gray-700">AIアシスタント</span>
          <div class="flex gap-2">
            <button @click="assistantState = assistantState === 'fullscreen' ? 'panel' : 'fullscreen'"
                    class="text-xs text-gray-500 hover:text-gray-700" x-text="assistantState === 'fullscreen' ? '小さくする' : '全画面へ'"></button>
            <button @click="assistantState = 'collapsed'" class="text-xs text-gray-500 hover:text-gray-700">✕</button>
          </div>
        </div>
        <div class="flex-1 overflow-y-auto space-y-3 p-3" x-ref="assistantChatLog">
          <template x-for="(msg, idx) in assistantMessages" :key="idx">
            <div :class="msg.role === 'user' ? 'text-right' : 'text-left'">
              <template x-if="msg.role !== 'pending_action'">
                <div :class="msg.role === 'user' ? 'inline-block bg-blue-600 text-white rounded-lg px-3 py-2 text-base' : 'inline-block bg-gray-100 text-gray-800 rounded-lg px-3 py-2 text-base'">
                  <p x-text="msg.content" class="whitespace-pre-line"></p>
                  <template x-if="msg.sources && msg.sources.length > 0">
                    <p class="text-xs text-gray-400 mt-1">
                      出典: <template x-for="(s, i) in msg.sources" :key="i"><span x-text="s.source_path + (s.heading ? ' - ' + s.heading : '') + (i < msg.sources.length - 1 ? '、' : '')"></span></template>
                    </p>
                  </template>
                </div>
              </template>
              <template x-if="msg.role === 'pending_action'">
                <div class="inline-block bg-yellow-50 border border-yellow-300 rounded-lg px-3 py-2 text-base">
                  <p x-text="msg.content" class="whitespace-pre-line"></p>
                  <p class="text-xs text-gray-600 mt-1 font-medium" x-text="msg.tool_name"></p>
                  <template x-for="(v, k) in msg.tool_args" :key="k">
                    <p class="text-xs text-gray-700"><span x-text="k"></span>: <span class="font-medium" x-text="JSON.stringify(v)"></span></p>
                  </template>
                  <div class="mt-2 flex gap-2" x-show="!msg.dismissed && !msg.resolved">
                    <button @click="confirmAssistantAction(msg)" :disabled="msg.executing" class="bg-blue-600 text-white text-xs px-3 py-1 rounded disabled:opacity-50">実行する</button>
                    <button @click="msg.dismissed = true" :disabled="msg.executing" class="text-gray-500 text-xs px-3 py-1 disabled:opacity-50">キャンセル</button>
                  </div>
                  <p x-show="msg.dismissed" class="text-xs text-gray-400 mt-1">キャンセルしました</p>
                  <p x-show="msg.resolved" class="text-xs text-green-600 mt-1">実行しました</p>
                </div>
              </template>
            </div>
          </template>
          <div x-show="assistantLoading" class="text-left text-xs text-gray-400">考え中...</div>
        </div>
        <form @submit.prevent="sendAssistantMessage()" class="flex gap-2 p-2 pt-2 border-t-2 border-gray-200">
          <input type="text" x-model="assistantInput" placeholder="質問を入力..."
                 class="flex-1 border-2 border-blue-300 bg-blue-50 rounded px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400" :disabled="assistantLoading">
          <button type="submit" class="bg-blue-600 text-white px-3 py-1.5 rounded text-sm" :disabled="assistantLoading || !assistantInput">送信</button>
        </form>
      </div>
    </div>
    <button x-show="assistantState === 'collapsed'" @click="assistantState = 'panel'"
            class="fixed bottom-4 right-4 z-50 bg-blue-600 hover:bg-blue-700 text-white rounded-full w-14 h-14 shadow-lg flex items-center justify-center text-2xl"
            style="display: none;">💬</button>
```

- [ ] **Step 2: Alpine状態とメソッドを追加する**

`portalApp()`関数の`return { ... }`オブジェクト内、既存の状態変数群（`tokenForm`等が定義されている付近）に以下を追記する:

```javascript
        assistantState: 'collapsed', assistantMessages: [], assistantInput: '', assistantLoading: false,
```

`portalApp()`の`return`オブジェクト内のメソッド群（`init()`と同じレベル）に以下を追記する:

```javascript
        async sendAssistantMessage() {
          const question = this.assistantInput;
          if (!question) return;
          this.assistantMessages.push({ role: 'user', content: question });
          this.assistantInput = '';
          this.assistantLoading = true;
          try {
            const result = await portalFetch('POST', '/me/assistant/chat', { message: question });
            if (result.pending_action) {
              this.assistantMessages.push({
                role: 'pending_action', content: result.answer,
                pending_action_id: result.pending_action.pending_action_id,
                tool_name: result.pending_action.tool_name,
                tool_args: result.pending_action.tool_args,
                dismissed: false,
              });
            } else {
              this.assistantMessages.push({ role: 'assistant', content: result.answer, sources: result.sources });
            }
          } catch(e) {
            this.assistantMessages.push({ role: 'assistant', content: 'エラー: ' + e.message });
          } finally {
            this.assistantLoading = false;
          }
        },
        async confirmAssistantAction(msg) {
          msg.executing = true;
          this.assistantLoading = true;
          try {
            await portalFetch('POST', '/me/assistant/confirm-action', { pending_action_id: msg.pending_action_id });
            msg.resolved = true;
            this.assistantMessages.push({ role: 'assistant', content: '実行しました。' });
          } catch(e) {
            this.assistantMessages.push({ role: 'assistant', content: 'エラー: ' + e.message });
          } finally {
            msg.executing = false;
            this.assistantLoading = false;
          }
        },
```

（`portalFetch`は同ファイル内の既存ヘルパー関数`async function portalFetch(method, path, body = null)`をそのまま使う。`admin-ui/js/api.js`への依存は追加しない。）

- [ ] **Step 3: HTMLタグ整合性を確認する**

Run:
```bash
python3 -c "
import re
content = open('admin-ui/tenant-portal.html', encoding='utf-8').read()
for tag in ['div','button','template','form','input','p']:
    opens = len(re.findall(r'<'+tag+r'[\s>]', content))
    closes = len(re.findall(r'</'+tag+r'>', content))
    void_ok = tag == 'input'
    print(tag, opens, closes, 'OK' if (opens==closes or void_ok) else 'MISMATCH')
"
```
Expected: `input`以外は`opens == closes`

- [ ] **Step 4: Tailwind CSSをビルドする**

Run: `npm run build:css`（リポジトリルートで実行）
Expected: `Done in ...ms.`（エラーなし）

- [ ] **Step 5: 手動UI確認**

開発環境を起動できる場合、テナントポータルにログインし以下を確認する:
1. 画面右下に常時、丸いチャットアイコン（💬）が表示されているか
2. クリックすると小窓が開き、ドラッグでリサイズできるか（右下角）
3. 「全画面へ」で画面全体に展開され、「小さくする」で戻るか
4. 質問すると回答が返るか、操作依頼で確認カードが表示され「実行する」で反映されるか
5. ✕ボタンで閉じてcollapsed状態に戻るか
6. ブラウザのコンソールにJSエラーが出ていないこと

実施できない場合はその旨を明示的に報告する。

- [ ] **Step 6: コミット**

```bash
git add admin-ui/tenant-portal.html admin-ui/static/tailwind.css
git commit -m "feat: テナントポータルに常時表示AIアシスタントウィジェットを追加"
```

---

### Task 5: 元設計書への拡張レシピ追記＋最終確認

**Files:**
- Modify: `docs/superpowers/specs/2026-09-12-rag-assistant-design.md`

- [ ] **Step 1: §8（機能拡張の進め方）にテナント向け分岐を追記する**

`docs/superpowers/specs/2026-09-12-rag-assistant-design.md`の「## 8. 機能拡張の進め方」節の末尾（既存の手順リストの後）に、以下の段落を追記する:

```markdown

**テナント管理者向けドメインを追加する場合の分岐（2026-09-13追記）:**
PF管理者向け（`app/services/rag_tools/<domain>.py`）とは別に、`app/services/rag_tools/tenant/<domain>.py`にテナント自己サービスAPI（`tenant_portal.py`）をラップするツールを追加する。ハンドラは`(tenant_id: str, payload: dict, **kwargs) -> dict`という統一シグネチャとし、`payload`（認証済みテナントJWTペイロード）をそのまま`tenant_portal.py`の関数に渡す（合成payloadは使わない）。`TENANT_TOOLS`（`app/services/rag_tools/tenant/__init__.py`）に追記する。詳細は`docs/superpowers/specs/2026-09-13-tenant-assistant-design.md`を参照。
```

- [ ] **Step 2: コミット**

```bash
git add docs/superpowers/specs/2026-09-12-rag-assistant-design.md
git commit -m "docs: 元設計書にテナント向けドメイン追加の拡張レシピを追記"
```

- [ ] **Step 3: 全体テストスイートを実行する**

Run: `cd core-api && python -m pytest tests/test_rag.py tests/test_rag_tools.py tests/test_rag_tools_tenant.py tests/test_tenant_portal_assistant.py tests/test_rag_api.py tests/test_rag_indexing.py tests/test_doc_chunking.py tests/test_ollama_client.py tests/test_assistant_settings.py -v`
Expected: 全件PASS

- [ ] **Step 4: `docker compose config`で構文検証する**

Run: `docker compose config --quiet`
Expected: エラーなし

- [ ] **Step 5: 完了報告**

全タスクの完了、Task 4の手動UI確認が実施できたかどうかをユーザーに報告し、pushしてよいか確認する。
