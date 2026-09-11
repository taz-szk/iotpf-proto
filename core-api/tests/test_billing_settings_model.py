from app.models.billing import BillingSettings


def test_billing_settings_model_has_expected_columns():
    assert BillingSettings.__tablename__ == "billing_settings"
    columns = {c.name for c in BillingSettings.__table__.columns}
    assert columns == {"id", "tax_rate"}
    assert BillingSettings.__table__.columns["id"].primary_key is True
