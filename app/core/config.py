from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    mongodb_uri: str
    mongodb_database: str = "syncra-db"
    auth0_domain: str
    auth0_audience: str
    app_base_url: str = "https://syncra-frontend.deeprajb012.workers.dev"
    invitation_ttl_days: int = Field(default=7, ge=1)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def auth0_issuer(self) -> str:
        return f"https://{self.auth0_domain}/"

    @property
    def auth0_jwks_url(self) -> str:
        return f"https://{self.auth0_domain}/.well-known/jwks.json"

    @property
    def auth0_userinfo_url(self) -> str:
        return f"https://{self.auth0_domain}/userinfo"


settings = Settings()
