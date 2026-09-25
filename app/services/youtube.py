"""استخراج معرّف الفيديو وجلب النص (Transcript) من يوتيوب.

ترتيب اختيار النص:
1. نص بإحدى اللغات المفضّلة (اليدوي أولًا ثم التلقائي).
2. إن لم يوجد: أول نص متاح (يدوي ثم تلقائي) مترجَمًا إلى اللغة المفضّلة الأولى إن أمكن.
3. إن تعذّرت الترجمة: أول نص متاح بلغته الأصلية.

يمكن تشغيله من سطر الأوامر:
    python -m app.services.youtube "https://youtu.be/VIDEO_ID" --lang ar --timestamps
"""

import re
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlparse

from requests import RequestException
from youtube_transcript_api import (
    AgeRestricted,
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    NoTranscriptFound,
    RequestBlocked,
    Transcript,
    TranscriptList,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig

from app.config import settings

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# رسائل مفهومة للمستخدم بدل أسماء الاستثناءات
_ERROR_MESSAGES: list[tuple[type[CouldNotRetrieveTranscript], str]] = [
    (TranscriptsDisabled, "النص المفرّغ معطّل لهذا الفيديو"),
    (NoTranscriptFound, "لا يوجد نص مفرّغ متاح لهذا الفيديو"),
    (VideoUnavailable, "الفيديو غير متاح أو محذوف"),
    (InvalidVideoId, "معرّف الفيديو غير صالح"),
    (AgeRestricted, "الفيديو مقيّد بالعمر ولا يمكن جلب نصه"),
    (RequestBlocked, "يوتيوب حظر الطلب من هذا الخادم؛ جرّب إعداد YOUTUBE_PROXY_URL"),
]


class TranscriptError(Exception):
    """خطأ عند تعذّر جلب نص الفيديو."""


@dataclass
class TranscriptSegment:
    text: str
    start: float
    duration: float


@dataclass
class TranscriptResult:
    video_id: str
    language: str
    language_code: str
    is_generated: bool
    is_translated: bool
    segments: list[TranscriptSegment]

    @property
    def text(self) -> str:
        """النص كاملًا في سطر واحد (مناسب للتلخيص)."""
        return " ".join(s.text for s in self.segments)

    def to_dict(self) -> dict:
        return {**asdict(self), "text": self.text}


def extract_video_id(url: str) -> str:
    """يستخرج معرّف الفيديو من الأشكال الشائعة لروابط يوتيوب."""
    url = url.strip()
    if _VIDEO_ID_RE.match(url):
        return url

    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").removeprefix("www.").removeprefix("m.")

    candidate = None
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        else:
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
                candidate = parts[1]

    if not candidate or not _VIDEO_ID_RE.match(candidate):
        raise ValueError("رابط يوتيوب غير صالح")
    return candidate


def _build_api() -> YouTubeTranscriptApi:
    proxy = settings.youtube_proxy_url
    if proxy:
        return YouTubeTranscriptApi(
            proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy)
        )
    return YouTubeTranscriptApi()


def select_transcript(
    transcript_list: TranscriptList, languages: list[str]
) -> tuple[Transcript, bool]:
    """يختار أنسب نص من القائمة. يعيد (النص، هل تُرجم؟)."""
    try:
        return transcript_list.find_transcript(languages), False
    except NoTranscriptFound:
        pass

    available = list(transcript_list)  # اليدوي أولًا ثم التلقائي
    if not available:
        raise NoTranscriptFound(transcript_list.video_id, languages, transcript_list)

    if languages:
        target = languages[0]
        for transcript in available:
            if transcript.is_translatable and any(
                t.language_code == target for t in transcript.translation_languages
            ):
                return transcript.translate(target), True

    return available[0], False


def get_transcript(url_or_id: str, languages: list[str] | None = None) -> TranscriptResult:
    """يجلب نص أي فيديو يوتيوب من رابطه أو معرّفه."""
    video_id = extract_video_id(url_or_id)
    languages = languages or settings.transcript_languages

    try:
        transcript_list = _build_api().list(video_id)
        transcript, translated = select_transcript(transcript_list, languages)
        fetched = transcript.fetch()
    except CouldNotRetrieveTranscript as exc:
        message = next(
            (msg for exc_type, msg in _ERROR_MESSAGES if isinstance(exc, exc_type)),
            f"تعذّر جلب نص الفيديو ({exc.__class__.__name__})",
        )
        raise TranscriptError(message) from exc
    except RequestException as exc:
        raise TranscriptError("تعذّر الاتصال بيوتيوب؛ تحقّق من الشبكة أو إعداد البروكسي") from exc

    return TranscriptResult(
        video_id=video_id,
        language=fetched.language,
        language_code=fetched.language_code,
        # الترجمة الآلية تُعلَّم دائمًا كـ generated في المكتبة، لذا نأخذ القيمة من المصدر
        is_generated=transcript.is_generated if not translated else True,
        is_translated=translated,
        segments=[
            TranscriptSegment(text=s.text, start=s.start, duration=s.duration)
            for s in fetched.snippets
            if s.text.strip()
        ],
    )


def fetch_transcript(video_id: str, languages: list[str]) -> str:
    """يجلب نص الفيديو كسلسلة نصية واحدة (يستخدمه المُلخِّص)."""
    return get_transcript(video_id, languages).text


def format_timestamp(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="جلب نص فيديو يوتيوب")
    parser.add_argument("url", help="رابط الفيديو أو معرّفه")
    parser.add_argument("--lang", action="append", help="لغة مفضّلة (يمكن تكرارها)")
    parser.add_argument("--timestamps", action="store_true", help="إظهار التوقيت لكل مقطع")
    args = parser.parse_args()

    try:
        result = get_transcript(args.url, args.lang)
    except (ValueError, TranscriptError) as exc:
        sys.exit(f"خطأ: {exc}")

    kind = "مترجَم" if result.is_translated else ("تلقائي" if result.is_generated else "يدوي")
    print(f"# {result.video_id} — {result.language} ({result.language_code}) — {kind}\n")
    if args.timestamps:
        for seg in result.segments:
            print(f"[{format_timestamp(seg.start)}] {seg.text}")
    else:
        print(result.text)


if __name__ == "__main__":
    _main()
