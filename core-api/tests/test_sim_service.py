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


def test_bind_line_device_id_containing_imsi_substring():
    """Regression test: device_id containing 'imsi' substring must not be misclassified
    when checking for UNIQUE constraint violations."""
    conn = _conn()
    conn.execute.return_value.fetchone.return_value = (1,)
    orig = MagicMock()
    # This detail is for device_id UNIQUE violation, not imsi
    orig.diag.message_detail = "Key (device_id)=(sensor-imsi-999) already exists."
    conn.execute.side_effect = [MagicMock(fetchone=lambda: (1,)), IntegrityError("stmt", {}, orig)]
    with patch("app.services.sim_service.ensure_sim_tables_to_tenant_schema"), \
         patch("app.services.sim_service.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        with pytest.raises(DeviceAlreadyBoundError):
            bind_line(TENANT_ID, "440100000099", "sensor-imsi-999")
