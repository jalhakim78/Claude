"""تمييز كل زائر بمعرّف عشوائي محفوظ في ملف تعريف ارتباط (Cookie)."""

import re
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

COOKIE_NAME = "yt2x_vid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # سنة
_VALID_ID = re.compile(r"^[0-9a-f]{32}$")


def set_visitor_cookie(response, visitor_id: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        visitor_id,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )


class VisitorMiddleware(BaseHTTPMiddleware):
    """يضع request.state.visitor_id لكل طلب، وينشئ الكوكي للزائر الجديد."""

    async def dispatch(self, request: Request, call_next):
        current = request.cookies.get(COOKIE_NAME, "")
        is_new = not _VALID_ID.match(current)
        request.state.visitor_id = uuid.uuid4().hex if is_new else current

        response = await call_next(request)
        # قد يغيّر مسار الدفع هوية الزائر (request.state.set_visitor_id)
        new_id = getattr(request.state, "set_visitor_id", None)
        if new_id:
            set_visitor_cookie(response, new_id)
        elif is_new:
            set_visitor_cookie(response, request.state.visitor_id)
        return response


def visitor_id(request: Request) -> str:
    return request.state.visitor_id
