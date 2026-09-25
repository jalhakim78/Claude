"""استخراج معرّف الفيديو وجلب النص (Transcript) من يوتيوب."""

import re
from urllib.parse import parse_qs, urlparse

from youtube_transcript_api import CouldNotRetrieveTranscript, YouTubeTranscriptApi

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


class TranscriptError(Exception):
    """خطأ عند تعذّر جلب نص الفيديو."""


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
    elif host in ("youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [None])[0]
        else:
            parts = parsed.path.strip("/").split("/")
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
                candidate = parts[1]

    if not candidate or not _VIDEO_ID_RE.match(candidate):
        raise ValueError("رابط يوتيوب غير صالح")
    return candidate


def fetch_transcript(video_id: str, languages: list[str]) -> str:
    """يجلب نص الفيديو كسلسلة نصية واحدة."""
    try:
        transcript = YouTubeTranscriptApi().fetch(video_id, languages=languages)
    except CouldNotRetrieveTranscript as exc:
        raise TranscriptError(f"تعذّر جلب نص الفيديو: {exc.__class__.__name__}") from exc
    return " ".join(snippet.text for snippet in transcript)
