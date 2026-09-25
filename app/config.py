"""إعدادات التطبيق المقروءة من متغيرات البيئة (أو من ملف .env)."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class Settings:
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-opus-5")
    transcript_languages: list[str] = field(
        default_factory=lambda: _split_csv(os.getenv("TRANSCRIPT_LANGUAGES", "ar,en"))
    )
    max_posts: int = int(os.getenv("MAX_POSTS", "10"))
    youtube_proxy_url: str | None = os.getenv("YOUTUBE_PROXY_URL") or None

    # الموقع والخطة المجانية
    site_name: str = os.getenv("SITE_NAME", "YT2X")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    free_summary_limit: int = int(os.getenv("FREE_SUMMARY_LIMIT", "3"))
    database_path: str = os.getenv("DATABASE_PATH", "data/app.db")

    # Stripe
    stripe_secret_key: str | None = os.getenv("STRIPE_SECRET_KEY") or None
    stripe_price_id: str | None = os.getenv("STRIPE_PRICE_ID") or None
    stripe_webhook_secret: str | None = os.getenv("STRIPE_WEBHOOK_SECRET") or None
    pro_price_label: str = os.getenv("PRO_PRICE_LABEL", "$9 / شهريًا")

    @property
    def stripe_enabled(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_price_id)

    @property
    def secure_cookies(self) -> bool:
        return self.public_base_url.startswith("https://")


settings = Settings()
