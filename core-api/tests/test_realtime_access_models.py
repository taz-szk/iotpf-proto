from app.models.realtime_access import RealtimeAccessToken

def test_realtime_access_token_table_name_and_columns():
    assert RealtimeAccessToken.__tablename__ == "realtime_access_tokens"
    row = RealtimeAccessToken(
        tenant_id="11111111-1111-1111-1111-111111111111",
        influx_authorization_id="auth-1",
        encrypted_token="enc-value",
    )
    assert row.influx_authorization_id == "auth-1"
    assert row.revoked_at is None
