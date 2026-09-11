from unittest.mock import MagicMock, patch

from app.services.bill_shock import check_and_notify_bill_shock


def _tenant(threshold=None, name="Acme Corp", tenant_id="tenant-1"):
    t = MagicMock()
    t.id = tenant_id
    t.name = name
    t.bill_shock_threshold_amount = threshold
    return t


def test_skips_when_no_effective_threshold():
    tenant = _tenant(threshold=None)
    invoice = MagicMock(total_amount=999999, bill_shock_notified_at=None)
    mock_db = MagicMock()
    with patch("app.services.bill_shock.get_effective_bill_shock_threshold", return_value=None), \
         patch("app.services.bill_shock.write_audit_log") as mock_audit, \
         patch("app.services.bill_shock.send_bill_shock_email") as mock_mail:
        check_and_notify_bill_shock(mock_db, tenant, "tenant_x", invoice)
    mock_audit.assert_not_called()
    mock_mail.assert_not_called()


def test_skips_when_under_threshold():
    tenant = _tenant(threshold=100000)
    invoice = MagicMock(total_amount=50000, bill_shock_notified_at=None)
    mock_db = MagicMock()
    with patch("app.services.bill_shock.get_effective_bill_shock_threshold", return_value=100000), \
         patch("app.services.bill_shock.write_audit_log") as mock_audit, \
         patch("app.services.bill_shock.send_bill_shock_email") as mock_mail:
        check_and_notify_bill_shock(mock_db, tenant, "tenant_x", invoice)
    mock_audit.assert_not_called()
    mock_mail.assert_not_called()


def test_skips_when_exactly_at_threshold():
    tenant = _tenant(threshold=100000)
    invoice = MagicMock(total_amount=100000, bill_shock_notified_at=None)
    mock_db = MagicMock()
    with patch("app.services.bill_shock.get_effective_bill_shock_threshold", return_value=100000), \
         patch("app.services.bill_shock.write_audit_log") as mock_audit, \
         patch("app.services.bill_shock.send_bill_shock_email") as mock_mail:
        check_and_notify_bill_shock(mock_db, tenant, "tenant_x", invoice)
    mock_audit.assert_not_called()
    mock_mail.assert_not_called()


def test_skips_when_already_notified_this_month():
    tenant = _tenant(threshold=100000)
    invoice = MagicMock(total_amount=150000, bill_shock_notified_at="2026-09-05T00:00:00Z")
    mock_db = MagicMock()
    with patch("app.services.bill_shock.get_effective_bill_shock_threshold", return_value=100000), \
         patch("app.services.bill_shock.write_audit_log") as mock_audit, \
         patch("app.services.bill_shock.send_bill_shock_email") as mock_mail:
        check_and_notify_bill_shock(mock_db, tenant, "tenant_x", invoice)
    mock_audit.assert_not_called()
    mock_mail.assert_not_called()


def test_notifies_on_first_excess_and_sets_notified_at():
    tenant = _tenant(threshold=100000, name="Acme Corp", tenant_id="tenant-1")
    invoice = MagicMock(target_year_month="2026-09", total_amount=150000, bill_shock_notified_at=None)
    mock_db = MagicMock()
    with patch("app.services.bill_shock.get_effective_bill_shock_threshold", return_value=100000), \
         patch("app.services.bill_shock.write_audit_log") as mock_audit, \
         patch("app.services.bill_shock._get_tenant_admin_emails", return_value=["tenant-admin@example.com"]), \
         patch("app.services.bill_shock._get_platform_admin_emails", return_value=["pf-admin@example.com"]), \
         patch("app.services.bill_shock.send_bill_shock_email") as mock_mail:
        check_and_notify_bill_shock(mock_db, tenant, "tenant_x", invoice)

    mock_audit.assert_called_once()
    audit_kwargs = mock_audit.call_args
    assert audit_kwargs.args[1] == "system"
    assert audit_kwargs.kwargs["tenant_id"] == "tenant-1"

    mock_mail.assert_called_once_with(
        to_emails=["tenant-admin@example.com", "pf-admin@example.com"],
        tenant_name="Acme Corp", target_year_month="2026-09",
        total_amount=150000, threshold_amount=100000,
    )
    assert invoice.bill_shock_notified_at is not None
    mock_db.commit.assert_called_once()


def test_get_tenant_admin_emails_queries_tenant_schema():
    from app.services.bill_shock import _get_tenant_admin_emails
    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: mock_conn
    mock_conn.__exit__ = MagicMock(return_value=False)
    row = MagicMock(email="admin@tenant.example.com")
    mock_conn.execute.return_value.fetchall.return_value = [row]
    with patch("app.services.bill_shock.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        result = _get_tenant_admin_emails("tenant_x")
    assert result == ["admin@tenant.example.com"]
    sql = str(mock_conn.execute.call_args[0][0])
    assert "tenant_x" in sql and "role" in sql and "is_active" in sql


def test_get_platform_admin_emails_queries_active_platform_users():
    from app.services.bill_shock import _get_platform_admin_emails
    row = MagicMock(email="pf-admin@example.com")
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.all.return_value = [row]
    result = _get_platform_admin_emails(mock_db)
    assert result == ["pf-admin@example.com"]
