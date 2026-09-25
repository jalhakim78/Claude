"""تسجيل الدخول بالبريد الإلكتروني دون كلمة مرور: رمز من 6 أرقام صالح لدقائق.

التدفق:
1. POST /api/auth/request-code {email}: نولّد رمزًا ونرسله بالبريد (نحفظ مجزّأه فقط).
2. POST /api/auth/verify {email, code}: نتحقق من الرمز، ثم:
   - إن كان للبريد حساب: ندمج فيه استخدام هذا المتصفح واشتراكه (إن وُجد) ونفتح جلسة عليه.
   - وإلا: يصبح الزائر الحالي حسابًا مرتبطًا بهذا البريد.
   وفي الحالتين نغيّر الكوكي المجهول لهذا المتصفح حتى لا يبقى معرّف الحساب في أي كوكي.
3. POST /api/auth/logout: يحذف الجلسة.
"""

import hmac
import logging
import re
import secrets
import smtplib
import ssl
import time
import uuid
from email.message import EmailMessage

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app import db
from app.config import settings
from app.visitors import SESSION_COOKIE, hash_token

log = logging.getLogger(__name__)
router = APIRouter()

CODE_TTL = 10 * 60          # صلاحية الرمز: 10 دقائق
MAX_ATTEMPTS = 5            # محاولات إدخال خاطئة قبل إبطال الرمز
RESEND_COOLDOWN = 60        # ثانية بين كل طلبين لنفس البريد
MAX_CODES_PER_HOUR = 5
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class EmailIn(BaseModel):
    email: str


class VerifyIn(BaseModel):
    email: str
    code: str


def normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="أدخل بريدًا إلكترونيًا صحيحًا")
    return email


def _code_hash(email: str, code: str) -> str:
    return hash_token(f"{email}:{code}")


def _require_enabled() -> None:
    if not settings.login_enabled:
        raise HTTPException(status_code=503, detail="تسجيل الدخول غير مُفعّل على هذا الخادم بعد")


def send_code_email(email: str, code: str) -> None:
    minutes = CODE_TTL // 60
    if not settings.email_enabled:
        # DEV_EMAIL_LOG: للتطوير المحلي فقط
        log.warning("DEV login code for %s: %s", email, code)
        return

    msg = EmailMessage()
    msg["Subject"] = f"رمز الدخول إلى {settings.site_name}: {code}"
    msg["From"] = settings.email_from
    msg["To"] = email
    msg.set_content(
        f"رمز الدخول إلى {settings.site_name} هو: {code}\n\n"
        f"الرمز صالح لمدة {minutes} دقائق. إن لم تطلب هذا الرمز فتجاهل الرسالة.\n"
    )
    msg.add_alternative(
        f"""<div dir="rtl" style="font-family:Tahoma,Arial,sans-serif;font-size:16px;color:#14161c">
  <p>رمز الدخول إلى <b>{settings.site_name}</b>:</p>
  <p style="font-size:32px;font-weight:700;letter-spacing:8px;direction:ltr;text-align:right">{code}</p>
  <p style="color:#646b7a">الرمز صالح لمدة {minutes} دقائق. إن لم تطلب هذا الرمز فتجاهل الرسالة.</p>
</div>""",
        subtype="html",
    )

    context = ssl.create_default_context()
    if settings.smtp_security == "ssl":
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
    with server:
        if settings.smtp_security != "ssl":
            server.starttls(context=context)
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password or "")
        server.send_message(msg)


@router.post("/api/auth/request-code")
async def request_code(payload: EmailIn):
    _require_enabled()
    email = normalize_email(payload.email)
    now = time.time()

    if db.count_codes_since(email, now - RESEND_COOLDOWN) > 0:
        raise HTTPException(status_code=429, detail="انتظر دقيقة قبل طلب رمز جديد")
    if db.count_codes_since(email, now - 3600) >= MAX_CODES_PER_HOUR:
        raise HTTPException(status_code=429, detail="طلبت رموزًا كثيرة، حاول بعد ساعة")

    code = f"{secrets.randbelow(10**6):06d}"
    db.log_code_request(email, now)
    db.add_login_code(email, _code_hash(email, code), now + CODE_TTL, now)
    try:
        await run_in_threadpool(send_code_email, email, code)
    except (OSError, smtplib.SMTPException) as exc:
        log.exception("Sending login code failed")
        db.delete_login_codes(email)
        raise HTTPException(status_code=502, detail="تعذّر إرسال البريد، حاول لاحقًا") from exc
    return {"sent": True, "expires_in": CODE_TTL}


@router.post("/api/auth/verify")
async def verify_code(payload: VerifyIn, request: Request):
    _require_enabled()
    email = normalize_email(payload.email)
    code = re.sub(r"\D", "", payload.code)

    row = db.get_login_code(email)
    invalid = HTTPException(status_code=400, detail="الرمز غير صحيح أو منتهي الصلاحية")
    if row is None or row["expires_at"] < time.time() or row["attempts"] >= MAX_ATTEMPTS:
        raise invalid
    if len(code) != 6 or not hmac.compare_digest(row["code_hash"], _code_hash(email, code)):
        db.add_code_attempt(row["id"])
        raise invalid
    db.delete_login_codes(email)

    current = request.state.visitor_id
    account_id = db.find_by_email(email)
    if account_id is None:
        if request.state.email:
            # مسجّل بحساب آخر: ننشئ حسابًا جديدًا للبريد الجديد
            account_id = uuid.uuid4().hex
            db.create_account(account_id, email, used=0)
        else:
            account_id = current
            db.claim_email(account_id, email)
    elif account_id != current and not request.state.email:
        db.merge_into(current, account_id)

    token = secrets.token_urlsafe(32)
    now = time.time()
    db.create_session(hash_token(token), account_id, now + settings.session_days * 86400, now)
    request.state.new_session_token = token
    # معرّف مجهول جديد لهذا المتصفح، حتى لا يبقى معرّف الحساب في الكوكي المجهول
    request.state.new_anon_id = uuid.uuid4().hex

    return {"email": email, "usage": db.get_usage(account_id).to_dict()}


@router.post("/api/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.delete_session(hash_token(token))
    request.state.clear_session = True
    request.state.new_anon_id = uuid.uuid4().hex
    return {"ok": True}
