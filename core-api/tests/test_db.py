from unittest.mock import patch, MagicMock
from app.database import create_tenant_schema


def _sql_text(call_args_list):
    """execute() 呼び出し引数（TextClause）から実SQL文字列を抽出して連結する。
    str(call(...)) はオブジェクトのrepr（メモリアドレス）を返すだけでSQL本文を含まないため使用しない。"""
    return " ".join(str(c.args[0]) if c.args else str(c) for c in call_args_list)


def test_create_tenant_schema_executes_sql():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)

    with patch("app.database.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        create_tenant_schema("123e4567-e89b-12d3-a456-426614174000")

    assert mock_conn.execute.called
    first_call = mock_conn.execute.call_args_list[0]
    assert "CREATE SCHEMA" in str(first_call)


def test_create_tenant_schema_includes_device_groups():
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    with patch("app.database.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        create_tenant_schema("123e4567-e89b-12d3-a456-426614174000")
    sql_calls = _sql_text(mock_conn.execute.call_args_list)
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

    sql_calls = _sql_text(alter_conn.execute.call_args_list)
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
    sql_calls = _sql_text(mock_conn.execute.call_args_list)
    assert "ADD COLUMN IF NOT EXISTS group_id" in sql_calls
    assert "dashboard_panel_configs_tenant_group_sensor_key_key" in sql_calls
