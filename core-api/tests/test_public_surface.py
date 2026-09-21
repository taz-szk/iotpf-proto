from app.config import settings


def test_api_docs_are_disabled_by_default(client):
    assert settings.enable_api_docs is False
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
