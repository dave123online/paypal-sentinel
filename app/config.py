from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_environment: str = "sandbox"
    paypal_webhook_id: str = ""

    google_api_key: str = ""
    google_model: str = "gemini-3.5-flash-lite"

    app_base_url: str = "http://127.0.0.1:8000"   # URL publique (return_url PayPal)

    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def paypal_base_url(self) -> str:
        if self.paypal_environment.lower() == "sandbox":
            return "https://api-m.sandbox.paypal.com"
        return "https://api-m.paypal.com"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
