"""إعدادات التطبيق المقروءة من متغيرات البيئة (أو من ملف .env)."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-opus-5")
    transcript_languages: list[str] = field(
        default_factory=lambda: _split_csv(os.getenv("TRANSCRIPT_LANGUAGES", "ar,en"))
    )
    max_posts: int = int(os.getenv("MAX_POSTS", "5"))


settings = Settings()
