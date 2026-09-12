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
