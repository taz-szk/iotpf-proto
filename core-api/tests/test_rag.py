from unittest.mock import MagicMock, patch

import pytest

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


def test_execute_pending_action_returns_none_when_tenant_mismatch():
    """他テナントのpending_action_idを指定した場合、Noneが返ることを検証する（テナント越境防止のリグレッションテスト）。"""
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = None

    result = execute_pending_action(mock_db, tenant_id="tenant-2", pending_action_id="pending-1")

    assert result is None
    # mock.call のrepr()はSQLAlchemy式をコンパイルしないため、各引数を個別にstr()して
    # 実際にコンパイルされたSQL条件文字列(例: "agent_pending_actions.tenant_id = :tenant_id_1")を検証する。
    # これにより、将来filter()からtenant_id条件が誤って削除された場合にこのテストが失敗する。
    filter_call = mock_db.query.return_value.filter.call_args
    filter_args_strs = [str(arg) for arg in filter_call.args]
    assert any("tenant_id" in s for s in filter_args_strs)


def test_execute_pending_action_raises_when_tool_not_registered():
    pending = MagicMock()
    pending.id = "pending-1"
    pending.tenant_id = "tenant-1"
    pending.tool_name = "removed_tool"
    pending.tool_args = {}
    pending.expires_at.__gt__ = lambda self, other: True

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = pending

    with patch("app.services.rag.TOOLS") as mock_tools:
        mock_tools.__iter__.return_value = iter([])

        with pytest.raises(ValueError):
            execute_pending_action(mock_db, tenant_id="tenant-1", pending_action_id="pending-1")

    mock_db.delete.assert_called_once_with(pending)


def test_answer_question_generates_final_answer_when_tool_rounds_exhausted():
    """往復上限(2回)に達した場合、ツール無しでもう一度chatを呼び最終回答を生成する(仕様§4.1.5)。"""
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []

    tool_call_1 = {"id": "call-1", "function": {"name": "tenant_stats_get", "arguments": "{}"}}
    tool_call_2 = {"id": "call-2", "function": {"name": "tenant_stats_get", "arguments": "{}"}}
    responses = [
        {"role": "assistant", "content": None, "tool_calls": [tool_call_1]},
        {"role": "assistant", "content": None, "tool_calls": [tool_call_2]},
        {"role": "assistant", "content": "上限到達後の最終回答です", "tool_calls": None},
    ]

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", side_effect=responses), \
         patch("app.services.rag.TOOLS") as mock_tools:
        read_tool = MagicMock(read_only=True)
        read_tool.name = "tenant_stats_get"
        read_tool.handler = MagicMock(return_value={"device_count": 5})
        mock_tools.__iter__.return_value = iter([read_tool])

        result = answer_question(mock_db, tenant_id="tenant-1", message="統計は？", requested_by="admin@example.com")

    assert result["answer"] == "上限到達後の最終回答です"
    assert result["pending_action"] is None


def test_answer_question_continues_when_read_only_tool_raises():
    """read_onlyツールハンドラが例外を送出しても、チャット全体は落ちずエラー内容をモデルに渡して継続する。"""
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []

    tool_call = {"id": "call-1", "function": {"name": "tenant_invoice_get", "arguments": '{"target_year_month": "2026-01"}'}}
    responses = [
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "assistant", "content": "請求書が見つかりませんでした", "tool_calls": None},
    ]

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", side_effect=responses), \
         patch("app.services.rag.TOOLS") as mock_tools:
        read_tool = MagicMock(read_only=True)
        read_tool.name = "tenant_invoice_get"
        read_tool.handler = MagicMock(side_effect=RuntimeError("Invoice not found"))
        mock_tools.__iter__.return_value = iter([read_tool])

        result = answer_question(mock_db, tenant_id="tenant-1", message="1月の請求書は？", requested_by="admin@example.com")

    assert result["answer"] == "請求書が見つかりませんでした"


def test_answer_question_strips_tenant_id_from_tool_args_before_storing():
    """モデルがtool_argsにtenant_idを混入させても、pending_actionへの保存前に除去される。"""
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []

    tool_call = {"id": "call-1", "function": {"name": "alert_rule_create", "arguments": '{"sensor_key": "temperature", "condition": "above", "tenant_id": "other-tenant"}'}}

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", return_value={"role": "assistant", "content": None, "tool_calls": [tool_call]}), \
         patch("app.services.rag.TOOLS") as mock_tools:
        action_tool = MagicMock(read_only=False)
        action_tool.name = "alert_rule_create"
        action_tool.description = "アラートルールを作成する"
        mock_tools.__iter__.return_value = iter([action_tool])

        result = answer_question(mock_db, tenant_id="tenant-1", message="アラート作って", requested_by="admin@example.com")

    added = mock_db.add.call_args[0][0]
    assert "tenant_id" not in added.tool_args
    assert result["pending_action"]["tool_args"] == {"sensor_key": "temperature", "condition": "above"}


def test_answer_question_includes_tool_call_id_in_tool_response_message():
    """OpenAI互換のtoolロールメッセージにtool_call_idが付与される。"""
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchall.return_value = []

    tool_call = {"id": "call-abc", "function": {"name": "tenant_stats_get", "arguments": "{}"}}
    responses = [
        {"role": "assistant", "content": None, "tool_calls": [tool_call]},
        {"role": "assistant", "content": "回答です", "tool_calls": None},
    ]

    with patch("app.services.rag.embed", return_value=[0.1] * 768), \
         patch("app.services.rag.chat", side_effect=responses) as mock_chat, \
         patch("app.services.rag.TOOLS") as mock_tools:
        read_tool = MagicMock(read_only=True)
        read_tool.name = "tenant_stats_get"
        read_tool.handler = MagicMock(return_value={"device_count": 5})
        mock_tools.__iter__.return_value = iter([read_tool])

        answer_question(mock_db, tenant_id="tenant-1", message="統計は？", requested_by="admin@example.com")

    second_call_messages = mock_chat.call_args_list[1].kwargs["messages"]
    tool_messages = [m for m in second_call_messages if m["role"] == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call-abc"


def test_execute_pending_action_returns_none_and_deletes_when_expired():
    """期限切れのpending_actionはNoneを返し、レコードを削除する（仕様§10の明示要件）。"""
    pending = MagicMock()
    pending.tool_name = "alert_rule_create"
    pending.expires_at.__gt__ = lambda self, other: False

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.first.return_value = pending

    result = execute_pending_action(mock_db, tenant_id="tenant-1", pending_action_id="pending-1")

    assert result is None
    mock_db.delete.assert_called_once_with(pending)
    mock_db.commit.assert_called_once()
