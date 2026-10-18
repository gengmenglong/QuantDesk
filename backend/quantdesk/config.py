from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='QD_', env_file='.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://quantdesk:quantdesk-local-test@127.0.0.1:55432/quantdesk'
    master_key: str = ''
    session_token: str = ''
    origins: str = 'http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,http://localhost:5173'
    mode: str = 'live'
    frontend_dir: str = 'frontend/dist'


@lru_cache
def settings():
    return Settings()
