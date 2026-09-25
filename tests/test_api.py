from fastapi.testclient import TestClient

from app import main
from app.schemas import VideoSummary

client = TestClient(main.app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_index_renders():
    response = client.get("/")
    assert response.status_code == 200
    assert "يوتيوب" in response.text


def test_invalid_url_returns_400():
    response = client.post("/api/summarize", json={"url": "https://example.com"})
    assert response.status_code == 400


def test_summarize_success(monkeypatch):
    monkeypatch.setattr(main, "fetch_transcript", lambda video_id, languages: "نص تجريبي")
    monkeypatch.setattr(
        main,
        "summarize",
        lambda transcript, language, num_posts: VideoSummary(
            title="عنوان", summary="ملخص", key_points=["نقطة"], x_posts=["منشور"] * num_posts
        ),
    )
    response = client.post(
        "/api/summarize",
        json={"url": "https://youtu.be/dQw4w9WgXcQ", "num_posts": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["video_id"] == "dQw4w9WgXcQ"
    assert len(body["x_posts"]) == 2
