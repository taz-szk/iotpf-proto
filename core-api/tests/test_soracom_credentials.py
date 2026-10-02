from unittest.mock import MagicMock, patch

import pytest

from app.services.soracom_credentials import (
    delete_credentials,
    get_credentials,
    get_credentials_summary,
    is_configured,
    save_credentials,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"


def _conn_with_row(row):
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    conn.execute.return_value.fetchone.return_value = row
    return conn


def test_get_credentials_returns_none_when_not_configured():
    conn = _conn_with_row(None)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        assert get_credentials(TENANT_ID) is None
        assert is_configured(TENANT_ID) is False


def test_get_credentials_decrypts_the_stored_key():
    from app.services.crypto import encrypt_secret
    row = MagicMock(coverage="jp", auth_key_id="keyId-abc", auth_key_enc=encrypt_secret("secret-xyz"))
    conn = _conn_with_row(row)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        creds = get_credentials(TENANT_ID)
    assert creds == {"coverage": "jp", "auth_key_id": "keyId-abc", "auth_key": "secret-xyz"}


def test_summary_never_includes_the_key_and_shows_a_hint():
    row = MagicMock(coverage="g", auth_key_id="keyId-abcdefgh1234")
    conn = _conn_with_row(row)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        summary = get_credentials_summary(TENANT_ID)
    assert summary == {"configured": True, "coverage": "g", "auth_key_id_hint": "...1234"}


def test_summary_when_not_configured():
    conn = _conn_with_row(None)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.connect.return_value = conn
        assert get_credentials_summary(TENANT_ID) == {
            "configured": False, "coverage": None, "auth_key_id_hint": None,
        }


def test_save_credentials_encrypts_before_storing():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        save_credentials(TENANT_ID, "jp", "keyId-new", "secret-new")
    sql, params = conn.execute.call_args.args
    assert "tenant_soracom_credentials" in str(sql)
    assert params["auth_key_id"] == "keyId-new"
    assert params["auth_key_enc"] != "secret-new"


def test_save_credentials_keeps_existing_key_when_both_omitted():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        save_credentials(TENANT_ID, "g", None, None)
    sql = str(conn.execute.call_args.args[0])
    assert "auth_key_enc" not in sql  # auth_key列を更新するUPDATE文には含まれない


def test_save_credentials_rejects_only_one_of_the_pair():
    with pytest.raises(ValueError):
        save_credentials(TENANT_ID, "jp", "keyId-only", None)
    with pytest.raises(ValueError):
        save_credentials(TENANT_ID, "jp", None, "secret-only")


def test_delete_credentials():
    conn = MagicMock()
    conn.__enter__ = lambda s: conn
    conn.__exit__ = MagicMock(return_value=False)
    with patch("app.services.soracom_credentials.engine") as mock_engine:
        mock_engine.begin.return_value = conn
        delete_credentials(TENANT_ID)
    sql = str(conn.execute.call_args.args[0])
    assert "DELETE FROM tenant_soracom_credentials" in sql
