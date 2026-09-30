from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WHY_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://why:why@localhost:5432/why"
    # Registration is restricted to repositories under this directory so the API
    # can't be used to read arbitrary paths on the host.
    repos_root: Path = Path("/repos")


@lru_cache
def get_settings() -> Settings:
    return Settings()
