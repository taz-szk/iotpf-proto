from unittest.mock import patch, MagicMock
import pytest
from app.services.realtime_access import issue_readonly_token, revoke_readonly_token, InfluxAuthorizationError

def test_issue_readonly_token_posts_read_only_permission_and_returns_id_and_token():
    resp = MagicMock(status_code=201)
    resp.json.return_value = {"id": "auth-123", "token": "plain-token-value"}
    resp.raise_for_status = MagicMock()
    with patch("app.services.realtime_access.httpx.post", return_value=resp) as mock_post:
        auth_id, token = issue_readonly_token("org-1")
    assert (auth_id, token) == ("auth-123", "plain-token-value")
    sent_json = mock_post.call_args.kwargs["json"]
    assert sent_json["permissions"] == [{"action": "read", "resource": {"type": "buckets", "orgID": "org-1"}}]

def test_issue_readonly_token_raises_on_influx_failure():
    resp = MagicMock(status_code=500)
    resp.raise_for_status.side_effect = Exception("boom")
    with patch("app.services.realtime_access.httpx.post", return_value=resp):
        with pytest.raises(InfluxAuthorizationError):
            issue_readonly_token("org-1")

def test_revoke_readonly_token_calls_delete():
    resp = MagicMock(status_code=204)
    resp.raise_for_status = MagicMock()
    with patch("app.services.realtime_access.httpx.delete", return_value=resp) as mock_delete:
        revoke_readonly_token("auth-123")
    assert "auth-123" in mock_delete.call_args.args[0]


def test_revoke_readonly_token_treats_404_as_success():
    resp = MagicMock(status_code=404)
    with patch("app.services.realtime_access.httpx.delete", return_value=resp):
        revoke_readonly_token("auth-gone")
