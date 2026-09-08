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
    row = MagicMock(id="g1", description="説明", created_at=None)
    row.name = "拠点A"
    conn = _conn()
    conn.execute.return_value.fetchall.return_value = [row]
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        result = list_groups(SCHEMA)
    assert result == [{"id": "g1", "name": "拠点A", "description": "説明", "created_at": None}]


def test_create_group_returns_new_group():
    row = MagicMock(id="g1", description=None, created_at=None)
    row.name = "拠点A"
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
