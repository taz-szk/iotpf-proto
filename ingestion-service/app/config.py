from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    postgres_dsn: str = ""
    influxdb_url: str = "http://influxdb:8086"
    influxdb_admin_token: str = ""
    tenant_cache_ttl_sec: int = 300
    # min_length: docker-compose passes "" (not unset) when EMQX_WEBHOOK_SECRET is
    # missing from .env; an empty/weak secret makes the HMAC check in main.py
    # trivially bypassable (hmac.compare_digest(b"", b"") == True).
    emqx_webhook_secret: str = Field(min_length=32)

    class Config:
        env_file = ".env"

settings = Settings()
