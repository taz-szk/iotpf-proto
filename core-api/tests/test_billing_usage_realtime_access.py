from unittest.mock import MagicMock
from app.services.billing_usage import _realtime_access_usage_snapshot

def test_realtime_access_fee_on_when_token_active():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(revoked_at=None)
    assert _realtime_access_usage_snapshot(db, "tenant-1") == {"realtime_access_fee": 1}

def test_realtime_access_fee_off_when_no_token():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    assert _realtime_access_usage_snapshot(db, "tenant-1") == {"realtime_access_fee": 0}

def test_realtime_access_fee_off_when_revoked():
    from datetime import datetime, timezone
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(
        revoked_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert _realtime_access_usage_snapshot(db, "tenant-1") == {"realtime_access_fee": 0}


def test_realtime_access_fee_off_when_tenant_suspended():
    db = MagicMock()
    def query(model):
        q = MagicMock()
        q.filter.return_value.first.return_value = (
            MagicMock(status="suspended") if model.__name__ == "Tenant" else MagicMock(revoked_at=None))
        return q
    db.query.side_effect = query
    assert _realtime_access_usage_snapshot(db, "tenant-1") == {"realtime_access_fee": 0}
