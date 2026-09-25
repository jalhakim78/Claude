"""نقطة دخول تطبيق FastAPI."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.schemas import SummarizeRequest, SummarizeResponse
from app.services.summarizer import SummarizerError, summarize
from app.services.youtube import TranscriptError, extract_video_id, fetch_transcript

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="YouTube Summarizer → X Posts")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"max_posts": settings.max_posts})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/summarize", response_model=SummarizeResponse)
async def summarize_video(payload: SummarizeRequest):
    try:
        video_id = extract_video_id(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    num_posts = min(payload.num_posts, settings.max_posts)
    try:
        transcript = await run_in_threadpool(
            fetch_transcript, video_id, settings.transcript_languages
        )
        result = await run_in_threadpool(summarize, transcript, payload.language, num_posts)
    except TranscriptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SummarizerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return SummarizeResponse(video_id=video_id, **result.model_dump())
