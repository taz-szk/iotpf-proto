from unittest.mock import patch, MagicMock
import pytest

from app.services.device_groups import (
    GroupInUseError,
    GroupNameConflictError,
    GroupNotFoundError,
    create_group,
    delete_group,
    list_groups,
    list_groups_with_devices,
    resync_grafana_groups,
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


def test_list_groups_with_devices_returns_names():
    group_row = MagicMock(id="g1")
    group_row.name = "拠点A"
    device_row1 = MagicMock()
    device_row1.device_name = "device-1"
    device_row2 = MagicMock()
    device_row2.device_name = "device-2"
    conn = _conn()
    conn.execute.return_value.fetchall.side_effect = [[group_row], [device_row1, device_row2]]
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        result = list_groups_with_devices(SCHEMA)
    assert result == [{"id": "g1", "name": "拠点A", "device_names": ["device-1", "device-2"]}]


def test_list_groups_with_devices_empty_group():
    group_row = MagicMock(id="g1")
    group_row.name = "拠点A"
    conn = _conn()
    conn.execute.return_value.fetchall.side_effect = [[group_row], []]
    with patch("app.services.device_groups.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        result = list_groups_with_devices(SCHEMA)
    assert result == [{"id": "g1", "name": "拠点A", "device_names": []}]


def test_resync_grafana_groups_calls_sync_with_org_and_groups():
    tenant = MagicMock(grafana_org_id="42")
    tenant.name = "acme"
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = tenant

    with patch("app.database.SessionLocal", return_value=mock_db), \
         patch("app.services.device_groups.list_groups_with_devices",
               return_value=[{"name": "拠点A", "device_names": []}]), \
         patch("app.services.grafana.sync_tenant_dashboard_groups") as mock_sync:
        resync_grafana_groups("tenant-1", SCHEMA)

    mock_sync.assert_called_once_with(42, "acme", [{"name": "拠点A", "device_names": []}])


def test_resync_grafana_groups_skips_when_no_grafana_org():
    tenant = MagicMock(grafana_org_id=None)
    mock_db = MagicMock()
    mock_db.__enter__ = lambda s: mock_db
    mock_db.__exit__ = MagicMock(return_value=False)
    mock_db.query.return_value.filter.return_value.first.return_value = tenant

    with patch("app.database.SessionLocal", return_value=mock_db), \
         patch("app.services.grafana.sync_tenant_dashboard_groups") as mock_sync:
        resync_grafana_groups("tenant-1", SCHEMA)

    mock_sync.assert_not_called()


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
