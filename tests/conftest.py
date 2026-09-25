import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """قاعدة بيانات مؤقتة لكل اختبار، وإعدادات Stripe فارغة افتراضيًا."""
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "free_summary_limit", 3)
    monkeypatch.setattr(settings, "stripe_secret_key", None)
    monkeypatch.setattr(settings, "stripe_price_id", None)
    monkeypatch.setattr(settings, "stripe_webhook_secret", None)
    monkeypatch.setattr(settings, "public_base_url", "http://yt2x.example")
    monkeypatch.setattr(settings, "site_name", "YT2X")
