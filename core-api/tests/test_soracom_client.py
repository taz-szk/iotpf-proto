from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services import soracom_client
from app.services.soracom_client import (
    SoracomApiError,
    SoracomAuthError,
    forget_token,
    get_subscriber,
    list_subscribers,
    verify_credentials,
    activate_subscriber,
    deactivate_subscriber,
    update_speed_class,
)

TENANT_ID = "22222222-2222-2222-2222-222222222222"
ARGS = (TENANT_ID, "jp", "keyId-test", "test-auth-key-secret")


@pytest.fixture(autouse=True)
def _clear_cache():
    forget_token(TENANT_ID)
    yield
    forget_token(TENANT_ID)


def _auth_response():
    r = MagicMock(status_code=200)
    r.json.return_value = {"apiKey": "api-xxx", "token": "token-xxx", "operatorId": "OP123"}
    return r


def test_list_subscribers_authenticates_then_lists_with_correct_headers_and_base_url():
    auth_resp = _auth_response()
    list_resp = MagicMock(status_code=200, headers={"x-soracom-next-key": "next-imsi-key"})
    list_resp.json.return_value = [{"imsi": "4401", "status": "active"}]
    with patch("app.services.soracom_client.httpx.post", return_value=auth_resp) as mock_post, \
         patch("app.services.soracom_client.httpx.get", return_value=list_resp) as mock_get:
        items, next_cursor = list_subscribers(*ARGS, limit=10)

    assert mock_post.call_args.args[0] == "https://api.soracom.io/v1/auth"
    assert mock_post.call_args.kwargs["json"] == {"authKeyId": "keyId-test", "authKey": "test-auth-key-secret"}
    get_args, get_kwargs = mock_get.call_args
    assert get_args[0] == "https://api.soracom.io/v1/subscribers"
    assert get_kwargs["headers"] == {"X-Soracom-API-Key": "api-xxx", "X-Soracom-Token": "token-xxx"}
    assert get_kwargs["params"]["limit"] == 10
    assert items == [{"imsi": "4401", "status": "active"}]
    assert next_cursor == "next-imsi-key"


def test_list_subscribers_uses_global_coverage_base_url():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()), \
         patch("app.services.soracom_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, headers={}, json=lambda: [])
        list_subscribers(TENANT_ID, "g", "keyId-test", "secret", limit=10)
    assert mock_get.call_args.args[0] == "https://g.api.soracom.io/v1/subscribers"


def test_list_subscribers_passes_filters_through():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()), \
         patch("app.services.soracom_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, headers={}, json=lambda: [])
        list_subscribers(*ARGS, status_filter="active|inactive", speed_class_filter="s1.standard",
                         tag_name="env", tag_value="prod", last_evaluated_key="prev-key")
    params = mock_get.call_args.kwargs["params"]
    assert params["status_filter"] == "active|inactive"
    assert params["speed_class_filter"] == "s1.standard"
    assert params["tag_name"] == "env" and params["tag_value"] == "prod"
    assert params["last_evaluated_key"] == "prev-key"


def test_auth_failure_raises_soracom_auth_error():
    with patch("app.services.soracom_client.httpx.post", return_value=MagicMock(status_code=401)):
        with pytest.raises(SoracomAuthError):
            list_subscribers(*ARGS)


def test_token_is_cached_across_calls():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()) as mock_post, \
         patch("app.services.soracom_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200, headers={}, json=lambda: [])
        list_subscribers(*ARGS)
        list_subscribers(*ARGS)
    assert mock_post.call_count == 1  # 2回目はキャッシュされたトークンを使う


def test_expired_token_triggers_one_reauth_and_retry():
    unauthorized = MagicMock(status_code=401, text="")
    ok = MagicMock(status_code=200, headers={}, json=lambda: [])
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()) as mock_post, \
         patch("app.services.soracom_client.httpx.get", side_effect=[unauthorized, ok]) as mock_get:
        items, _ = list_subscribers(*ARGS)
    assert mock_post.call_count == 2  # 最初の認証 + 401を受けての再認証
    assert mock_get.call_count == 2
    assert items == []


def test_get_subscriber_returns_none_on_404():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()), \
         patch("app.services.soracom_client.httpx.get", return_value=MagicMock(status_code=404, text="")):
        assert get_subscriber(*ARGS, "4401") is None


def test_network_timeout_raises_soracom_api_error_without_leaking_credentials():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()), \
         patch("app.services.soracom_client.httpx.get", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(SoracomApiError) as exc_info:
            list_subscribers(*ARGS)
    assert "test-auth-key-secret" not in str(exc_info.value)


def test_verify_credentials_true_and_false():
    with patch("app.services.soracom_client.httpx.post", return_value=_auth_response()):
        assert verify_credentials(*ARGS) is True
    forget_token(TENANT_ID)
    with patch("app.services.soracom_client.httpx.post", return_value=MagicMock(status_code=401)):
        assert verify_credentials(*ARGS) is False


def test_activate_subscriber_posts_to_the_activate_path():
    updated = MagicMock(status_code=200)
    updated.json.return_value = {"imsi": "4401", "status": "active"}
    with patch("app.services.soracom_client.httpx.post", side_effect=[_auth_response(), updated]) as mock_post:
        result = activate_subscriber(*ARGS, "4401")
    assert mock_post.call_args.args[0] == "https://api.soracom.io/v1/subscribers/4401/activate"
    assert result == {"imsi": "4401", "status": "active"}


def test_deactivate_subscriber_returns_none_on_404():
    not_found = MagicMock(status_code=404, text="")
    with patch("app.services.soracom_client.httpx.post", side_effect=[_auth_response(), not_found]):
        assert deactivate_subscriber(*ARGS, "0000") is None


def test_update_speed_class_sends_the_speed_class_body():
    updated = MagicMock(status_code=200)
    updated.json.return_value = {"imsi": "4401", "speedClass": "s1.fast"}
    with patch("app.services.soracom_client.httpx.post", side_effect=[_auth_response(), updated]) as mock_post:
        update_speed_class(*ARGS, "4401", "s1.fast")
    call_args, call_kwargs = mock_post.call_args
    assert call_args[0] == "https://api.soracom.io/v1/subscribers/4401/update_speed_class"
    assert call_kwargs["json"] == {"speedClass": "s1.fast"}
