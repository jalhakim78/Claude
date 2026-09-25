import pytest

from app.schemas import VideoSummary
from app.services import summarizer
from app.tones import TONES


@pytest.mark.parametrize("tone_id", list(TONES))
@pytest.mark.parametrize("language,expected", [("ar", "العربية"), ("en", "English")])
def test_system_prompt_includes_options(tone_id, language, expected):
    tone = TONES[tone_id]
    prompt = summarizer.build_system_prompt(language, tone, 7)
    assert expected in prompt
    assert tone.guide in prompt
    assert tone.platform in prompt
    assert "7 بالضبط" in prompt
    assert "مهما كانت لغة الفيديو" in prompt


def _summary(posts):
    return VideoSummary(title="t", summary="s", key_points=["k"], posts=posts)


def test_extra_posts_are_trimmed(monkeypatch):
    monkeypatch.setattr(summarizer, "_call", lambda *a: _summary(["a", "b", "c", "d"]))
    assert summarizer.summarize("نص", num_posts=2).posts == ["a", "b"]


def test_missing_posts_are_topped_up(monkeypatch):
    calls = []

    def fake_call(system, user, output_format):
        calls.append((system, user))
        if output_format is VideoSummary:
            return _summary(["a", "a", " ", "b"])  # مكرر وفارغ
        return summarizer._MorePosts(posts=["b", "c", "d"])

    monkeypatch.setattr(summarizer, "_call", fake_call)
    result = summarizer.summarize("نص", language="en", num_posts=4, tone_id="linkedin")
    assert result.posts == ["a", "b", "c", "d"]
    assert len(calls) == 2
    system, user = calls[1]
    assert "2" in system and "English" in system and "LinkedIn" in system
    assert "- a" in user and "- b" in user  # يمرّر المنشورات الموجودة لتجنّب التكرار


def test_top_up_gives_up_after_max_attempts(monkeypatch):
    calls = []

    def fake_call(system, user, output_format):
        calls.append(output_format)
        if output_format is VideoSummary:
            return _summary(["a"])
        return summarizer._MorePosts(posts=["a"])  # لا جديد

    monkeypatch.setattr(summarizer, "_call", fake_call)
    result = summarizer.summarize("نص", num_posts=3)
    assert result.posts == ["a"]
    assert len(calls) == 1 + summarizer.MAX_TOP_UP_ATTEMPTS
