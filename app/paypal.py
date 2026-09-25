"""الربط مع PayPal: اشتراكات عبر أزرار PayPal الذكية (Smart Buttons) وواجهة REST.

التدفق:
1. عند الضغط على زر PayPal تستدعي الواجهة POST /api/paypal/subscription، فينشئ الخادم
   الاشتراك ويربطه بالزائر (custom_id)، ويعيد معرّفه للأزرار.
2. بعد موافقة المستخدم تنقله الواجهة إلى /checkout?provider=paypal&subscription_id=...
   فيتحقق الخادم من الاشتراك لدى PayPal ويفعّل الخطة المدفوعة.
3. الـ Webhook (POST /api/paypal/webhook) يفعّل الخطة أو يلغيها حسب أحداث الاشتراك،
   بعد التحقق من توقيع الحدث لدى PayPal.
"""

import logging
import re
import threading
import time

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from app import db
from app.config import settings
from app.visitors import current_email, require_login_for_payment, visitor_id

log = logging.getLogger(__name__)
router = APIRouter()

TIMEOUT = 20
SUBSCRIPTION_ID_RE = re.compile(r"^I-[A-Z0-9]{6,32}$")
DOWNGRADE_EVENTS = {
    "BILLING.SUBSCRIPTION.CANCELLED",
    "BILLING.SUBSCRIPTION.SUSPENDED",
    "BILLING.SUBSCRIPTION.EXPIRED",
}


class PayPalError(Exception):
    pass


_token: dict = {"value": None, "expires_at": 0.0}
_token_lock = threading.Lock()


def _require_enabled() -> None:
    if not settings.paypal_enabled:
        raise HTTPException(status_code=503, detail="الدفع عبر PayPal غير مُفعّل على هذا الخادم بعد")


def _access_token() -> str:
    """رمز OAuth من PayPal، مخزّن مؤقتًا حتى قُبيل انتهاء صلاحيته."""
    with _token_lock:
        if _token["value"] and time.time() < _token["expires_at"]:
            return _token["value"]
        try:
            r = requests.post(
                f"{settings.paypal_api_base}/v1/oauth2/token",
                auth=(settings.paypal_client_id, settings.paypal_client_secret),
                data={"grant_type": "client_credentials"},
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            raise PayPalError("تعذّر الاتصال بـ PayPal") from exc
        if r.status_code != 200:
            log.error("PayPal OAuth failed: %s %s", r.status_code, r.text[:300])
            raise PayPalError("فشل التحقق من بيانات PayPal (Client ID / Secret)")
        body = r.json()
        _token["value"] = body["access_token"]
        _token["expires_at"] = time.time() + int(body.get("expires_in", 300)) - 60
        return _token["value"]


def _api(method: str, path: str, **kwargs) -> dict:
    try:
        r = requests.request(
            method,
            f"{settings.paypal_api_base}{path}",
            headers={"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"},
            timeout=TIMEOUT,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise PayPalError("تعذّر الاتصال بـ PayPal") from exc
    if r.status_code >= 400:
        log.error("PayPal %s %s failed: %s %s", method, path, r.status_code, r.text[:300])
        raise PayPalError(f"خطأ من PayPal ({r.status_code})")
    return r.json() if r.content else {}


def create_subscription(vid: str, email: str | None = None) -> str:
    payload = {
        "plan_id": settings.paypal_plan_id,
        # نربط الاشتراك بالزائر لنعرف من نفعّل له الخطة بعد الدفع
        "custom_id": vid,
        "application_context": {
            "brand_name": settings.site_name,
            "shipping_preference": "NO_SHIPPING",
            "user_action": "SUBSCRIBE_NOW",
            "return_url": f"{settings.public_base_url}/checkout?provider=paypal",
            "cancel_url": f"{settings.public_base_url}/?checkout=cancelled",
        },
    }
    if email:
        payload["subscriber"] = {"email_address": email}
    return _api("POST", "/v1/billing/subscriptions", json=payload)["id"]


def get_subscription(subscription_id: str) -> dict:
    return _api("GET", f"/v1/billing/subscriptions/{subscription_id}")


def is_active_for_our_plan(sub: dict) -> bool:
    return sub.get("status") == "ACTIVE" and sub.get("plan_id") == settings.paypal_plan_id


def activate_from_subscription(sub: dict) -> str | None:
    vid = sub.get("custom_id")
    if not vid:
        log.warning("PayPal subscription %s has no custom_id", sub.get("id"))
        return None
    db.activate_pro_paypal(vid, sub["id"])
    return vid


def verify_webhook(headers, event: dict) -> bool:
    if not settings.paypal_webhook_id:
        return False
    names = ["auth-algo", "cert-url", "transmission-id", "transmission-sig", "transmission-time"]
    values = {n: headers.get(f"paypal-{n}") for n in names}
    if not all(values.values()):
        return False
    result = _api(
        "POST",
        "/v1/notifications/verify-webhook-signature",
        json={
            "auth_algo": values["auth-algo"],
            "cert_url": values["cert-url"],
            "transmission_id": values["transmission-id"],
            "transmission_sig": values["transmission-sig"],
            "transmission_time": values["transmission-time"],
            "webhook_id": settings.paypal_webhook_id,
            "webhook_event": event,
        },
    )
    return result.get("verification_status") == "SUCCESS"


@router.post("/api/paypal/subscription")
async def paypal_create_subscription(request: Request):
    _require_enabled()
    require_login_for_payment(request)
    try:
        sub_id = await run_in_threadpool(create_subscription, visitor_id(request), current_email(request))
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"id": sub_id}


@router.post("/api/paypal/webhook")
async def paypal_webhook(request: Request):
    _require_enabled()
    try:
        event = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    try:
        verified = await run_in_threadpool(verify_webhook, request.headers, event)
    except PayPalError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not verified:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event.get("event_type", "")
    resource = event.get("resource") or {}
    sub_id = resource.get("id")
    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED" and is_active_for_our_plan(resource):
        activate_from_subscription(resource)
    elif event_type in DOWNGRADE_EVENTS and sub_id:
        db.set_plan_by_paypal_subscription(sub_id, db.PLAN_FREE)

    return {"received": True}
