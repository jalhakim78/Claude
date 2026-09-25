"""الربط مع Stripe، وصفحة نجاح الدفع /checkout المشتركة بين Stripe وPayPal.

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

from app import db, paypal
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


INVALID_LINK = "رابط غير صالح. إن كنت قد دفعت بالفعل فسيتم تفعيل حسابك تلقائيًا خلال دقائق."
SUCCESS = "تم تفعيل اشتراكك بنجاح! استمتع بتلخيص غير محدود."
PENDING = "لم تكتمل عملية الدفع بعد."


async def _stripe_result(session_id: str | None) -> tuple[str, str, str | None]:
    if not session_id or not session_id.startswith("cs_"):
        return "error", INVALID_LINK, None
    try:
        session = await run_in_threadpool(_client().v1.checkout.sessions.retrieve, session_id)
    except stripe.StripeError:
        log.exception("Stripe session retrieval failed")
        return "error", "لم نتمكن من التحقق من عملية الدفع.", None

    if _is_paid(session):
        paid_vid = _activate_from_session(session)
        if paid_vid:
            return "success", SUCCESS, paid_vid
    elif _get(session, "status") == "open":
        return "pending", PENDING, None
    return "error", "لم نتمكن من التحقق من عملية الدفع.", None


async def _paypal_result(subscription_id: str | None) -> tuple[str, str, str | None]:
    paypal._require_enabled()
    if not subscription_id or not paypal.SUBSCRIPTION_ID_RE.match(subscription_id):
        return "error", INVALID_LINK, None
    try:
        sub = await run_in_threadpool(paypal.get_subscription, subscription_id)
    except paypal.PayPalError:
        return "error", "لم نتمكن من التحقق من الاشتراك لدى PayPal.", None

    if paypal.is_active_for_our_plan(sub):
        paid_vid = paypal.activate_from_subscription(sub)
        if paid_vid:
            return "success", SUCCESS, paid_vid
    elif sub.get("status") in ("APPROVAL_PENDING", "APPROVED"):
        return "pending", "تمت الموافقة على الاشتراك وجارٍ تفعيله لدى PayPal؛ حدّث الصفحة بعد دقيقة.", None
    return "error", "الاشتراك غير نشط لدى PayPal.", None


@router.get("/checkout", response_class=HTMLResponse)
async def checkout_return(
    request: Request,
    provider: str = "stripe",
    session_id: str | None = None,
    subscription_id: str | None = None,
):
    """صفحة نجاح الدفع لكلتا البوابتين: تتحقق من الدفع لدى البوابة ثم تفعّل الخطة المدفوعة.

    Stripe: /checkout?session_id=cs_...
    PayPal: /checkout?provider=paypal&subscription_id=I-...
    """
    if provider == "paypal":
        status, message, paid_vid = await _paypal_result(subscription_id)
    else:
        status, message, paid_vid = await _stripe_result(session_id)

    # إن أكمل الدفع من متصفح مختلف، نربط هذا المتصفح بالحساب المدفوع
    if paid_vid and paid_vid != visitor_id(request):
        request.state.set_visitor_id = paid_vid

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
