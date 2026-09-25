import pytest
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
        lambda transcript, language, num_posts, tone: VideoSummary(
            title="عنوان", summary="ملخص", key_points=["نقطة"],
            posts=[f"{tone}-{language}-{i}" for i in range(num_posts)],
        ),
    )
    response = client.post(
        "/api/summarize",
        json={
            "url": "https://youtu.be/dQw4w9WgXcQ",
            "num_posts": 7,
            "language": "en",
            "tone": "linkedin",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["video_id"] == "dQw4w9WgXcQ"
    assert body["posts"][0] == "linkedin-en-0"
    assert len(body["posts"]) == body["requested_posts"] == 7
    assert (body["tone"], body["language"], body["char_limit"]) == ("linkedin", "en", 3000)


@pytest.mark.parametrize(
    "extra",
    [{"num_posts": 0}, {"num_posts": 11}, {"num_posts": 2.5}, {"tone": "funny"}, {"language": "fr"}],
)
def test_summarize_rejects_invalid_options(extra):
    response = client.post("/api/summarize", json={"url": "https://youtu.be/dQw4w9WgXcQ", **extra})
    assert response.status_code == 422


def test_index_renders_controls():
    html = client.get("/").text
    assert '<select id="language"' in html
    assert 'type="number" id="num-posts"' in html and 'max="10"' in html
    for tone in ("linkedin", "x", "marketing"):
        assert f'id="tone-{tone}"' in html


def test_transcript_endpoint(monkeypatch):
    from app.services.youtube import TranscriptResult, TranscriptSegment

    monkeypatch.setattr(
        main,
        "get_transcript",
        lambda url, lang: TranscriptResult(
            video_id="dQw4w9WgXcQ", language="Arabic", language_code="ar",
            is_generated=True, is_translated=False,
            segments=[TranscriptSegment("مرحبا", 0.0, 1.5), TranscriptSegment("بكم", 1.5, 1.0)],
        ),
    )
    response = client.get("/api/transcript", params={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "مرحبا بكم"
    assert len(body["segments"]) == 2


def test_transcript_endpoint_invalid_url():
    response = client.get("/api/transcript", params={"url": "not-a-url"})
    assert response.status_code == 400
