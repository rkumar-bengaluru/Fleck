import os
from dataclasses import dataclass
from functools import lru_cache


def setting(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str
    anthropic_api_key: str
    google_client_id: str
    google_client_secret: str
    google_mode: str
    allowed_domains: str
    allowed_emails: str
    live_fallback: str
    secret_key: str
    claude_model: str
    claude_fast_model: str


@lru_cache
def get_settings() -> Settings:
    s = setting
    return Settings(
        database_url=s("FLECK_DATABASE_URL", "postgresql://fleck:fleck@localhost:5432/fleck"),
        anthropic_api_key=s("ANTHROPIC_API_KEY", ""),
        google_client_id=s("GOOGLE_CLIENT_ID", ""),
        google_client_secret=s("GOOGLE_CLIENT_SECRET", ""),
        google_mode=s("FLECK_GOOGLE_MODE", "gsim"),
        allowed_domains=s("FLECK_ALLOWED_DOMAINS", ""),
        allowed_emails=s("FLECK_ALLOWED_EMAILS", ""),
        live_fallback=s("FLECK_LIVE_FALLBACK", "claude"),
        secret_key=s("FLECK_SECRET_KEY", ""),
        claude_model=s("FLECK_CLAUDE_MODEL", "claude-sonnet-5-5"),
        claude_fast_model=s("FLECK_CLAUDE_FAST_MODEL", "claude-haiku-5-5"),
    )
