"""削除済み/未登録デバイスのMQTT接続を拒否するための、デバイス登録確認(device_access)のテスト。"""
from unittest.mock import patch, MagicMock

import pytest

from app.services import device_access

_TENANT = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def _clear_cache():
    device_access._cache.clear()
    yield
    device_access._cache.clear()


def _db(found: bool):
    db = MagicMock()
    db.__enter__ = lambda s: db
    db.__exit__ = MagicMock(return_value=False)
    db.execute.return_value.first.return_value = (1,) if found else None
    return db


def test_registered_device_is_allowed_and_cached():
    db = _db(True)
    with patch("app.services.device_access.SessionLocal", return_value=db):
        assert device_access.is_device_registered(_TENANT, "dev-001") is True
        assert device_access.is_device_registered(_TENANT, "dev-001") is True
    assert db.execute.call_count == 1


def test_unregistered_device_is_denied():
    with patch("app.services.device_access.SessionLocal", return_value=_db(False)):
        assert device_access.is_device_registered(_TENANT, "gone") is False


def test_forget_device_drops_cached_positive_result():
    with patch("app.services.device_access.SessionLocal", return_value=_db(True)):
        assert device_access.is_device_registered(_TENANT, "dev-001") is True
    device_access.forget_device(_TENANT, "dev-001")
    with patch("app.services.device_access.SessionLocal", return_value=_db(False)):
        assert device_access.is_device_registered(_TENANT, "dev-001") is False


def test_db_error_fails_closed():
    with patch("app.services.device_access.SessionLocal", side_effect=Exception("db down")):
        assert device_access.is_device_registered(_TENANT, "dev-001") is False


def test_non_uuid_tenant_id_is_rejected_without_touching_db():
    with patch("app.services.device_access.SessionLocal") as mock_sl:
        assert device_access.is_device_registered('x"; DROP SCHEMA public; --', "dev-001") is False
    mock_sl.assert_not_called()


def test_positive_result_expires_after_ttl():
    db = _db(True)
    with patch("app.services.device_access.SessionLocal", return_value=db), \
         patch("app.services.device_access.time") as mock_time:
        mock_time.monotonic.return_value = 1000.0
        device_access.is_device_registered(_TENANT, "dev-001")
        mock_time.monotonic.return_value = 1000.0 + device_access._POSITIVE_TTL + 1
        device_access.is_device_registered(_TENANT, "dev-001")
    assert db.execute.call_count == 2
