from app.models.public import Tenant, TenantSignupRequest


def test_tenant_has_trial_columns():
    cols = {c.name for c in Tenant.__table__.columns}
    assert {"signup_source", "trial_expires_at", "trial_warning_sent_at",
            "trial_extension_requested_at"} <= cols


def test_tenant_signup_request_table_columns():
    cols = {c.name for c in TenantSignupRequest.__table__.columns}
    assert cols == {"id", "email", "tenant_name", "slug", "password_hash",
                     "verification_token", "created_at", "expires_at"}
