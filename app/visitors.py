"""هوية الزائر: كوكي مجهول لكل متصفح، وكوكي جلسة منفصل بعد تسجيل الدخول بالبريد.

- yt2x_vid: معرّف عشوائي لزائر مجهول (الحصة المجانية دون تسجيل دخول).
- yt2x_session: رمز جلسة عشوائي بعد تسجيل الدخول، ونحفظ مجزّأه فقط في قاعدة البيانات.

قاعدة أمان: الكوكي المجهول لا يمنح أبدًا الدخول إلى حساب مرتبط ببريد؛ الوصول للحساب
يكون عبر جلسة صالحة فقط.
"""

import hashlib
import re
import time
import uuid

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from app import db
from app.config import settings

COOKIE_NAME = "yt2x_vid"
SESSION_COOKIE = "yt2x_session"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # سنة
_VALID_ID = re.compile(r"^[0-9a-f]{32}$")
_VALID_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _set_cookie(response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name, value, max_age=max_age, httponly=True, samesite="lax", secure=settings.secure_cookies
    )


def _resolve(request: Request) -> tuple[str, bool, str | None]:
    """يعيد (معرّف الزائر الفعلي، هل هو معرّف مجهول جديد؟، بريد الحساب إن كان مسجّلًا)."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if _VALID_TOKEN.match(token):
        account_id = db.session_visitor(hash_token(token), time.time())
        if account_id:
            return account_id, False, db.get_email(account_id)

    anon = request.cookies.get(COOKIE_NAME, "")
    # معرّف غير صالح، أو معرّف أصبح حسابًا ببريد (لا يُفتح بالكوكي المجهول)
    if not _VALID_ID.match(anon) or db.get_email(anon):
        return uuid.uuid4().hex, True, None
    return anon, False, None


class VisitorMiddleware(BaseHTTPMiddleware):
    """يضع request.state.visitor_id و request.state.email لكل طلب."""

    async def dispatch(self, request: Request, call_next):
        vid, new_anon, email = await run_in_threadpool(_resolve, request)
        request.state.visitor_id = vid
        request.state.email = email
        request.state.anon_id = request.cookies.get(COOKIE_NAME, "") if email else vid

        response = await call_next(request)

        rotated = getattr(request.state, "new_anon_id", None)
        if rotated:
            _set_cookie(response, COOKIE_NAME, rotated, COOKIE_MAX_AGE)
        elif new_anon:
            _set_cookie(response, COOKIE_NAME, vid, COOKIE_MAX_AGE)

        token = getattr(request.state, "new_session_token", None)
        if token:
            _set_cookie(response, SESSION_COOKIE, token, settings.session_days * 86400)
        elif getattr(request.state, "clear_session", False) or (
            request.cookies.get(SESSION_COOKIE) and not email
        ):
            response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax", secure=settings.secure_cookies)
        return response


def visitor_id(request: Request) -> str:
    return request.state.visitor_id


def current_email(request: Request) -> str | None:
    return request.state.email


def require_login_for_payment(request: Request) -> None:
    """نربط الاشتراك ببريد المستخدم حتى يستعيده من أي جهاز بتسجيل الدخول."""
    if settings.login_enabled and not current_email(request):
        raise HTTPException(status_code=401, detail="سجّل الدخول ببريدك الإلكتروني أولًا لربط اشتراكك به")
