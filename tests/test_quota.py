from fastapi.testclient import TestClient

from app import db, main
from app.config import settings
from app.schemas import Quote, VideoSummary
from app.services.summarizer import SummarizerError
from app.visitors import COOKIE_NAME

URL = "https://youtu.be/dQw4w9WgXcQ"


def _fake(transcript, opts):
    return VideoSummary(
        title="t", summary="s", key_points=["k"],
        posts=["منشور" + opts.watermark_suffix], quotes=[Quote(text="q", translation="")],
    )


def _setup(monkeypatch, summarize=_fake):
    monkeypatch.setattr(main, "fetch_transcript", lambda *a: "نص")
    monkeypatch.setattr(main, "summarize", summarize)


def test_new_visitor_gets_cookie_and_free_quota():
    c = TestClient(main.app)
    r = c.get("/api/me")
    assert COOKIE_NAME in r.cookies
    assert r.json()["usage"] == {"plan": "free", "used": 0, "limit": 3, "remaining": 3}


def test_three_free_summaries_then_locked(monkeypatch):
    _setup(monkeypatch)
    c = TestClient(main.app)
    remaining = [c.post("/api/summarize", json={"url": URL}).json()["usage"]["remaining"] for _ in range(3)]
    assert remaining == [2, 1, 0]

    r = c.post("/api/summarize", json={"url": URL})
    assert r.status_code == 402
    assert c.get("/api/me").json()["usage"]["remaining"] == 0

    # زائر آخر (كوكي مختلف) لديه حصته الخاصة
    assert TestClient(main.app).post("/api/summarize", json={"url": URL}).status_code == 200


def test_failed_summary_is_not_counted(monkeypatch):
    def boom(*a):
        raise SummarizerError("x")

    _setup(monkeypatch, boom)
    c = TestClient(main.app)
    assert c.post("/api/summarize", json={"url": URL}).status_code == 502
    assert c.get("/api/me").json()["usage"]["used"] == 0


def test_invalid_request_is_not_counted(monkeypatch):
    _setup(monkeypatch)
    c = TestClient(main.app)
    c.post("/api/summarize", json={"url": "bad"})
    c.post("/api/summarize", json={"url": URL, "num_posts": 50})
    assert c.get("/api/me").json()["usage"]["used"] == 0


def test_free_plan_gets_watermark_pro_does_not(monkeypatch):
    _setup(monkeypatch)
    c = TestClient(main.app)
    body = c.post("/api/summarize", json={"url": URL}).json()
    assert body["watermarked"] is True
    assert "YT2X" in body["posts"][0] and settings.public_base_url in body["posts"][0]

    db.activate_pro(c.cookies[COOKIE_NAME], "cus_1", "sub_1")
    body = c.post("/api/summarize", json={"url": URL, "language": "en"}).json()
    assert body["watermarked"] is False
    assert body["posts"] == ["منشور"]
    assert body["usage"]["plan"] == "pro" and body["usage"]["remaining"] is None


def test_pro_is_unlimited(monkeypatch):
    _setup(monkeypatch)
    c = TestClient(main.app)
    c.get("/")
    db.activate_pro(c.cookies[COOKIE_NAME], None, None)
    for _ in range(5):
        assert c.post("/api/summarize", json={"url": URL}).status_code == 200


def test_forged_cookie_is_replaced():
    c = TestClient(main.app, cookies={COOKIE_NAME: "../../etc"})
    r = c.get("/api/me")
    assert r.cookies[COOKIE_NAME] != "../../etc"


def test_cookie_is_secure_on_https(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "https://yt2x.example")
    header = TestClient(main.app, base_url="https://testserver").get("/api/me").headers["set-cookie"]
    assert "Secure" in header and "HttpOnly" in header and "samesite=lax" in header.lower()
