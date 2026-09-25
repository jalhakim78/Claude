"""نقطة دخول تطبيق FastAPI."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.schemas import MAX_POSTS, SummarizeRequest, SummarizeResponse, TranscriptResponse
from app.services.summarizer import SummarizerError, summarize
from app.tones import DEFAULT_TONE, TONES
from app.services.youtube import (
    TranscriptError,
    extract_video_id,
    fetch_transcript,
    get_transcript,
)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="YouTube Summarizer → X Posts")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"max_posts": _max_posts(), "tones": TONES.values(), "default_tone": DEFAULT_TONE},
    )


def _max_posts() -> int:
    return max(1, min(settings.max_posts, MAX_POSTS))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/transcript", response_model=TranscriptResponse)
async def transcript(
    url: str = Query(..., description="رابط فيديو يوتيوب أو معرّفه"),
    lang: list[str] | None = Query(None, description="اللغات المفضّلة بالترتيب"),
):
    try:
        result = await run_in_threadpool(get_transcript, url, lang)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TranscriptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()


@app.post("/api/summarize", response_model=SummarizeResponse)
async def summarize_video(payload: SummarizeRequest):
    try:
        video_id = extract_video_id(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.num_posts > _max_posts():
        raise HTTPException(
            status_code=422, detail=f"الحد الأقصى لعدد المنشورات هو {_max_posts()}"
        )
    try:
        transcript = await run_in_threadpool(
            fetch_transcript, video_id, settings.transcript_languages
        )
        result = await run_in_threadpool(
            summarize, transcript, payload.language, payload.num_posts, payload.tone
        )
    except TranscriptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SummarizerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return SummarizeResponse(
        video_id=video_id,
        language=payload.language,
        tone=payload.tone,
        char_limit=TONES[payload.tone].char_limit,
        requested_posts=payload.num_posts,
        **result.model_dump(),
    )
