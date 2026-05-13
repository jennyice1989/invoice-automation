from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "A2Z Lightspeed Invoice Backend"
    admin_api_key: str = "change-me-before-deploy"
    cors_origins: str = "*"

    lightspeed_retailer_id: str | None = None
    lightspeed_access_token: str | None = None

    openai_api_key: str | None = None
    dropbox_token: str | None = None

    storage_dir: str = "storage"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
