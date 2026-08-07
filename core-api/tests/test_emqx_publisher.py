import json
from unittest.mock import patch, MagicMock
from app.services.emqx_publisher import publish_ota_command


def test_publish_ota_command_posts_to_emqx():
    with patch("app.services.emqx_publisher.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_httpx.post.return_value = mock_resp

        publish_ota_command("tenant-abc", "device-001", {"version": "1.0.0"})

    mock_httpx.post.assert_called_once()
    call_kwargs = mock_httpx.post.call_args
    assert "api/v5/publish" in call_kwargs[0][0]
    payload_sent = call_kwargs[1]["json"]
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
