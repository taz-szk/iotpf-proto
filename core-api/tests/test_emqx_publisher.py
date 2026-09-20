import json
from unittest.mock import patch, MagicMock
from app.services.emqx_publisher import publish_ota_command


def test_publish_ota_command_posts_to_emqx():
    # トークンはモジュール内にキャッシュされるため、他のテストの影響を受けないよう未取得の状態から始める
    with patch("app.services.emqx_publisher._token", None), \
         patch("app.services.emqx_publisher.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        publish_ota_command("tenant-abc", "device-001", {"version": "1.0.0"})

    # 1回目: EMQX APIへのログイン(トークン取得)、2回目: 実際のpublish
    assert mock_httpx.post.call_count == 2
    login_call, publish_call = mock_httpx.post.call_args_list
    assert "api/v5/login" in login_call[0][0]
    assert "api/v5/publish" in publish_call[0][0]
    payload_sent = publish_call[1]["json"]
    assert payload_sent["topic"] == "/tenant-abc/devices/device-001/commands"
    assert payload_sent["qos"] == 1


def test_ota_topic_matches_acl():
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        publish_ota_command("tenant-123", "dev-001", {"type": "ota", "version": "1.0"})

    call_kwargs = mock_post.call_args
    body = call_kwargs.kwargs["json"] if call_kwargs.kwargs else call_kwargs[1]["json"]
    assert body["topic"] == "/tenant-123/devices/dev-001/commands"
