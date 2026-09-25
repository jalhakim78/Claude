import pytest

from app.services.youtube import extract_video_id


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://www.youtube.com/embed/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "youtube.com/watch?v=dQw4w9WgXcQ",
        "dQw4w9WgXcQ",
    ],
)
def test_extract_video_id(url):
    assert extract_video_id(url) == "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", ["", "https://example.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/short"])
def test_extract_video_id_invalid(url):
    with pytest.raises(ValueError):
        extract_video_id(url)


# ---------- اختيار النص (بدون اتصال بالشبكة) ----------

from youtube_transcript_api import NoTranscriptFound, Transcript, TranscriptList
from youtube_transcript_api._transcripts import _TranslationLanguage

from app.services import youtube
from app.services.youtube import TranscriptError, select_transcript

VID = "dQw4w9WgXcQ"
AR = _TranslationLanguage(language="Arabic", language_code="ar")


def _t(code, generated=False, translations=()):
    return Transcript(None, VID, f"https://x/{code}", code.upper(), code, generated, list(translations))


def _list(manual=(), generated=()):
    return TranscriptList(
        VID,
        {t.language_code: t for t in manual},
        {t.language_code: t for t in generated},
        [AR],
    )


def test_prefers_requested_language_manual_first():
    tl = _list(manual=[_t("ar")], generated=[_t("en", True)])
    chosen, translated = select_transcript(tl, ["ar", "en"])
    assert (chosen.language_code, chosen.is_generated, translated) == ("ar", False, False)


def test_falls_back_to_generated_in_requested_language():
    tl = _list(manual=[_t("fr")], generated=[_t("en", True)])
    chosen, translated = select_transcript(tl, ["ar", "en"])
    assert (chosen.language_code, translated) == ("en", False)


def test_translates_when_no_requested_language():
    tl = _list(manual=[_t("fr", translations=[AR])])
    chosen, translated = select_transcript(tl, ["ar"])
    assert (chosen.language_code, translated) == ("ar", True)


def test_uses_original_when_not_translatable():
    tl = _list(generated=[_t("de", True)])
    chosen, translated = select_transcript(tl, ["ar"])
    assert (chosen.language_code, translated) == ("de", False)


def test_empty_list_raises():
    with pytest.raises(NoTranscriptFound):
        select_transcript(_list(), ["ar"])


def test_get_transcript_maps_errors(monkeypatch):
    class FakeApi:
        def list(self, video_id):
            raise NoTranscriptFound(video_id, ["ar"], _list())

    monkeypatch.setattr(youtube, "_build_api", lambda: FakeApi())
    with pytest.raises(TranscriptError, match="لا يوجد نص"):
        youtube.get_transcript(f"https://youtu.be/{VID}")


def test_get_transcript_maps_network_errors(monkeypatch):
    import requests

    class FakeApi:
        def list(self, video_id):
            raise requests.ConnectionError("boom")

    monkeypatch.setattr(youtube, "_build_api", lambda: FakeApi())
    with pytest.raises(TranscriptError, match="تعذّر الاتصال"):
        youtube.get_transcript(VID)


def test_format_timestamp():
    assert youtube.format_timestamp(65.4) == "01:05"
    assert youtube.format_timestamp(3725) == "1:02:05"
