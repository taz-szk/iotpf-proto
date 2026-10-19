import math
from unittest.mock import MagicMock
from app.services.billing_usage import _export_usage_snapshot

def test_export_usage_snapshot_base_fee_on_when_schedule_enabled():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = MagicMock(enabled=True)
    db.query.return_value.filter.return_value.all.return_value = []
    result = _export_usage_snapshot(db, "tenant-1")
    assert result["export_base_fee"] == 1

def test_export_usage_snapshot_base_fee_off_without_schedule():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    result = _export_usage_snapshot(db, "tenant-1")
    assert result["export_base_fee"] == 0

def test_export_usage_snapshot_sums_retained_size_rounded_up():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    rows = [MagicMock(file_size_bytes=1_500_000), MagicMock(file_size_bytes=600_000)]
    db.query.return_value.filter.return_value.all.return_value = rows
    result = _export_usage_snapshot(db, "tenant-1")
    assert result["export_retained_size_mb"] == math.ceil(2_100_000 / (1024 * 1024))
