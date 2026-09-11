from app.models.billing import BillingDefaultUnitPrice


def test_billing_default_unit_price_model_has_expected_columns():
    assert BillingDefaultUnitPrice.__tablename__ == "billing_default_unit_prices"
    columns = {c.name for c in BillingDefaultUnitPrice.__table__.columns}
    assert columns == {"item_key", "unit_price", "updated_at"}
    assert BillingDefaultUnitPrice.__table__.columns["item_key"].primary_key is True
