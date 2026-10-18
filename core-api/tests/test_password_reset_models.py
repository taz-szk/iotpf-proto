import uuid
from datetime import datetime, timedelta, timezone
from app.models.public import PasswordResetToken

def test_password_reset_token_fields():
    t = PasswordResetToken(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), user_id=uuid.uuid4(),
        email="user@example.com", token="tok123",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    assert t.email == "user@example.com"
    assert t.token == "tok123"
