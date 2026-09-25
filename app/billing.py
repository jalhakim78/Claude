"""الربط مع Stripe: إنشاء جلسة الدفع، صفحة العودة /checkout، والـ Webhook.

التدفق:
1. الواجهة تستدعي POST /api/checkout/session فتحصل على رابط Stripe Checkout.
2. بعد الدفع يعيد Stripe المستخدم إلى GET /checkout?session_id=...
   فنتحقق من الجلسة مباشرة من Stripe ونفعّل الخطة المدفوعة فورًا.
3. الـ Webhook (POST /api/stripe/webhook) هو المصدر الموثوق: يفعّل الخطة حتى لو أغلق
   المستخدم الصفحة قبل العودة، ويعيدها للمجانية عند إلغاء الاشتراك أو تعثّر الدفع.
"""

import logging
from pathlib import Path

import stripe
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.config import settings
from app.visitors import visitor_id

log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates")

# حالات الاشتراك التي تُبقي الخطة مدفوعة
ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


def _client() -> stripe.StripeClient:
    if not settings.stripe_enabled:
        raise HTTPException(status_code=503, detail="الدفع غير مُفعّل على هذا الخادم بعد")
    return stripe.StripeClient(settings.stripe_secret_key)


def _get(obj, key: str):
    """قراءة حقل من كائن Stripe بأمان (كائنات Stripe ليست dict)."""
    return obj[key] if obj is not None and key in obj else None


def _id(value) -> str | None:
    """بعض الحقول تكون معرّفًا نصيًا أو كائنًا موسّعًا."""
    if value is None or isinstance(value, str):
        return value
    return _get(value, "id")


def _is_paid(session) -> bool:
    return _get(session, "status") == "complete" and _get(session, "payment_status") in (
        "paid",
        "no_payment_required",
    )


def _activate_from_session(session) -> str | None:
    vid = _get(session, "client_reference_id")
    if not vid:
        log.warning("Checkout session %s has no client_reference_id", _get(session, "id"))
        return None
    db.activate_pro(vid, _id(_get(session, "customer")), _id(_get(session, "subscription")))
    return vid


@router.post("/api/checkout/session")
async def create_checkout_session(request: Request):
    client = _client()
    vid = visitor_id(request)
    try:
        session = await run_in_threadpool(
            client.v1.checkout.sessions.create,
            {
                "mode": "subscription",
                "line_items": [{"price": settings.stripe_price_id, "quantity": 1}],
                # نربط الدفع بالزائر لنعرف من نفعّل له الخطة بعد الدفع
                "client_reference_id": vid,
                "subscription_data": {"metadata": {"visitor_id": vid}},
                "success_url": f"{settings.public_base_url}/checkout?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url": f"{settings.public_base_url}/?checkout=cancelled",
                "allow_promotion_codes": True,
            },
        )
    except stripe.StripeError as exc:
        log.exception("Stripe checkout session creation failed")
        raise HTTPException(status_code=502, detail="تعذّر بدء عملية الدفع، حاول لاحقًا") from exc
    return {"url": session.url}


@router.get("/checkout", response_class=HTMLResponse)
async def checkout_return(request: Request, session_id: str | None = None):
    """الصفحة التي يعود إليها المستخدم من Stripe بعد الدفع."""
    status, message = "error", "لم نتمكن من التحقق من عملية الدفع."

    if not session_id or not session_id.startswith("cs_"):
        message = "رابط غير صالح. إن كنت قد دفعت بالفعل فسيتم تفعيل حسابك تلقائيًا خلال دقائق."
    else:
        try:
            session = await run_in_threadpool(_client().v1.checkout.sessions.retrieve, session_id)
        except HTTPException:
            raise
        except stripe.StripeError:
            log.exception("Stripe session retrieval failed")
            session = None

        if session is not None and _is_paid(session):
            paid_vid = _activate_from_session(session)
            if paid_vid:
                # إن أكمل الدفع من متصفح مختلف، نربط هذا المتصفح بالحساب المدفوع
                if paid_vid != visitor_id(request):
                    request.state.set_visitor_id = paid_vid
                status, message = "success", "تم تفعيل اشتراكك بنجاح! استمتع بتلخيص غير محدود."
        elif session is not None and _get(session, "status") == "open":
            status, message = "pending", "لم تكتمل عملية الدفع بعد."

    return templates.TemplateResponse(
        request,
        "checkout.html",
        {"status": status, "message": message, "site_name": settings.site_name},
        status_code=200 if status != "error" else 400,
    )


@router.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(
            payload, request.headers.get("stripe-signature"), settings.stripe_webhook_secret
        )
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid signature") from exc

    obj = event.data.object
    if event.type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if _is_paid(obj):
            _activate_from_session(obj)
    elif event.type in ("customer.subscription.updated", "customer.subscription.deleted"):
        active = event.type != "customer.subscription.deleted" and (
            _get(obj, "status") in ACTIVE_SUBSCRIPTION_STATUSES
        )
        db.set_plan_by_subscription(obj.id, db.PLAN_PRO if active else db.PLAN_FREE)

    return {"received": True}
