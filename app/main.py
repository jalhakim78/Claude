"""نقطة دخول تطبيق FastAPI."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db
from app.auth import router as auth_router
from app.billing import router as billing_router
from app.paypal import router as paypal_router
from app.config import settings
from app.schemas import MAX_POSTS, SummarizeRequest, SummarizeResponse, TranscriptResponse
from app.services.summarizer import (
    GenerationOptions,
    SummarizerError,
    summarize,
    watermark_text,
)
from app.services.youtube import (
    TranscriptError,
    extract_video_id,
    fetch_transcript,
    get_transcript,
)
from app.tones import DEFAULT_TONE, TONES
from app.visitors import VisitorMiddleware, current_email, visitor_id

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="YouTube Summarizer → X Posts", lifespan=lifespan)
app.add_middleware(VisitorMiddleware)
app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(paypal_router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _max_posts() -> int:
    return max(1, min(settings.max_posts, MAX_POSTS))


def _account(request: Request) -> dict:
    usage = db.get_usage(visitor_id(request))
    return {
        "usage": usage.to_dict(),
        "user": {"email": current_email(request)} if current_email(request) else None,
        "login_enabled": settings.login_enabled,
        "require_login": settings.require_login and settings.login_enabled,
        "stripe_enabled": settings.stripe_enabled,
        "price_label": settings.pro_price_label,
        # معرّف العميل (Client ID) عام بطبيعته ويُستخدم في المتصفح؛ السرّ يبقى في الخادم فقط
        "paypal": {
            "enabled": settings.paypal_enabled,
            "client_id": settings.paypal_client_id if settings.paypal_enabled else None,
            "currency": settings.paypal_currency,
        },
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "max_posts": _max_posts(),
            "tones": TONES.values(),
            "default_tone": DEFAULT_TONE,
            "account": _account(request),
            "site_name": settings.site_name,
        },
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/me")
def me(request: Request):
    """حالة الزائر: الخطة وعدد المحاولات المتبقية."""
    return _account(request)


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
async def summarize_video(payload: SummarizeRequest, request: Request):
    try:
        video_id = extract_video_id(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.num_posts > _max_posts():
        raise HTTPException(
            status_code=422, detail=f"الحد الأقصى لعدد المنشورات هو {_max_posts()}"
        )

    if settings.require_login and settings.login_enabled and not current_email(request):
        raise HTTPException(status_code=401, detail="سجّل الدخول ببريدك الإلكتروني لبدء التلخيص")

    vid = visitor_id(request)
    # نحجز المحاولة قبل البدء (ذريًّا) ونعيدها إن فشل التلخيص
    if not db.try_consume(vid):
        raise HTTPException(
            status_code=402,
            detail=f"استخدمت محاولاتك المجانية الـ {settings.free_summary_limit}. اشترك للمتابعة.",
        )
    usage = db.get_usage(vid)

    opts = GenerationOptions(
        language=payload.language,
        tone_id=payload.tone,
        num_posts=payload.num_posts,
        thread=payload.thread,
        watermark=None if usage.is_pro else watermark_text(payload.language),
    )
    try:
        text = await run_in_threadpool(fetch_transcript, video_id, settings.transcript_languages)
        result = await run_in_threadpool(summarize, text, opts)
    except (TranscriptError, SummarizerError) as exc:
        db.refund(vid)
        status = 422 if isinstance(exc, TranscriptError) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except Exception:
        db.refund(vid)
        raise

    return SummarizeResponse(
        video_id=video_id,
        language=payload.language,
        tone=payload.tone,
        char_limit=opts.char_limit,
        requested_posts=payload.num_posts,
        thread=payload.thread,
        watermarked=opts.watermark is not None,
        usage=usage.to_dict(),
        **result.model_dump(),
    )
