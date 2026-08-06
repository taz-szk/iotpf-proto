from unittest.mock import patch, MagicMock
from app.database import create_tenant_schema

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
