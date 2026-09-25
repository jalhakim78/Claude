import time

import pytest
from fastapi.testclient import TestClient

from app import auth, db, main
from app.config import settings
from app.schemas import Quote, VideoSummary
from app.visitors import COOKIE_NAME, SESSION_COOKIE

EMAIL = "user@example.com"
URL = "https://youtu.be/dQw4w9WgXcQ"


@pytest.fixture
def outbox(monkeypatch):
    """يفعّل الدخول ويلتقط الرموز المرسلة بدل إرسال بريد حقيقي."""
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "email_from", "YT2X <no-reply@example.com>")
    sent = []
    monkeypatch.setattr(auth, "send_code_email", lambda email, code: sent.append((email, code)))
    return sent


@pytest.fixture
def summarize_stub(monkeypatch):
    monkeypatch.setattr(main, "fetch_transcript", lambda *a: "نص")
    monkeypatch.setattr(main, "summarize", lambda t, o: VideoSummary(
        title="t", summary="s", key_points=[], posts=["p" + o.watermark_suffix], quotes=[]))


def _login(c, outbox, email=EMAIL):
    assert c.post("/api/auth/request-code", json={"email": email}).status_code == 200
    code = outbox[-1][1]
    r = c.post("/api/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 200, r.text
    return r


def _allow_resend(monkeypatch):
    monkeypatch.setattr(auth, "RESEND_COOLDOWN", 0)


# ---------- الإعداد ----------

def test_login_disabled_without_email_settings():
    c = TestClient(main.app)
    assert c.post("/api/auth/request-code", json={"email": EMAIL}).status_code == 503
    me = c.get("/api/me").json()
    assert me["login_enabled"] is False and me["user"] is None


def test_dev_email_log_enables_login(monkeypatch, caplog):
    monkeypatch.setattr(settings, "dev_email_log", True)
    c = TestClient(main.app)
    with caplog.at_level("WARNING"):
        assert c.post("/api/auth/request-code", json={"email": EMAIL}).status_code == 200
    assert "DEV login code for user@example.com" in caplog.text


# ---------- طلب الرمز ----------

@pytest.mark.parametrize("bad", ["", "no-at-sign", "a@b", "a b@c.com", "x" * 250 + "@a.com"])
def test_rejects_invalid_email(outbox, bad):
    assert TestClient(main.app).post("/api/auth/request-code", json={"email": bad}).status_code == 422
    assert outbox == []


def test_code_is_6_digits_and_stored_hashed(outbox):
    TestClient(main.app).post("/api/auth/request-code", json={"email": "  User@Example.COM "})
    email, code = outbox[0]
    assert email == EMAIL and len(code) == 6 and code.isdigit()
    row = db.get_login_code(EMAIL)
    assert code not in row["code_hash"]


def test_resend_cooldown_and_hourly_limit(outbox, monkeypatch):
    c = TestClient(main.app)
    assert c.post("/api/auth/request-code", json={"email": EMAIL}).status_code == 200
    assert c.post("/api/auth/request-code", json={"email": EMAIL}).status_code == 429
    _allow_resend(monkeypatch)
    for _ in range(auth.MAX_CODES_PER_HOUR - 1):
        assert c.post("/api/auth/request-code", json={"email": EMAIL}).status_code == 200
    assert c.post("/api/auth/request-code", json={"email": EMAIL}).status_code == 429


def test_smtp_failure_returns_502_and_discards_code(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "email_from", "a@example.com")

    def boom(email, code):
        raise OSError("connection refused")

    monkeypatch.setattr(auth, "send_code_email", boom)
    assert TestClient(main.app).post("/api/auth/request-code", json={"email": EMAIL}).status_code == 502
    assert db.get_login_code(EMAIL) is None


def test_send_code_email_uses_smtp(monkeypatch):
    for k, v in dict(smtp_host="smtp.example.com", smtp_port=587, smtp_user="u", smtp_password="p",
                     email_from="YT2X <no-reply@example.com>", smtp_security="starttls").items():
        monkeypatch.setattr(settings, k, v)
    calls = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls["connect"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, context): calls["tls"] = True
        def login(self, u, p): calls["login"] = (u, p)
        def send_message(self, msg): calls["msg"] = msg

    monkeypatch.setattr(auth.smtplib, "SMTP", FakeSMTP)
    auth.send_code_email(EMAIL, "123456")
    msg = calls["msg"]
    assert calls["connect"] == ("smtp.example.com", 587) and calls["tls"] and calls["login"] == ("u", "p")
    assert msg["To"] == EMAIL and "123456" in msg["Subject"]
    assert "123456" in msg.get_body(("plain",)).get_content()


# ---------- التحقق من الرمز ----------

def test_wrong_code_then_lockout(outbox):
    c = TestClient(main.app)
    c.post("/api/auth/request-code", json={"email": EMAIL})
    code = outbox[0][1]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(auth.MAX_ATTEMPTS):
        assert c.post("/api/auth/verify", json={"email": EMAIL, "code": wrong}).status_code == 400
    # بعد استنفاد المحاولات حتى الرمز الصحيح لا يعمل
    assert c.post("/api/auth/verify", json={"email": EMAIL, "code": code}).status_code == 400


def test_expired_code(outbox, monkeypatch):
    c = TestClient(main.app)
    c.post("/api/auth/request-code", json={"email": EMAIL})
    code = outbox[0][1]
    real = time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + auth.CODE_TTL + 1)
    assert c.post("/api/auth/verify", json={"email": EMAIL, "code": code}).status_code == 400


def test_code_is_single_use(outbox):
    c = TestClient(main.app)
    c.post("/api/auth/request-code", json={"email": EMAIL})
    code = outbox[0][1]
    assert c.post("/api/auth/verify", json={"email": EMAIL, "code": code}).status_code == 200
    assert TestClient(main.app).post("/api/auth/verify", json={"email": EMAIL, "code": code}).status_code == 400


def test_new_code_invalidates_previous(outbox, monkeypatch):
    _allow_resend(monkeypatch)
    c = TestClient(main.app)
    c.post("/api/auth/request-code", json={"email": EMAIL})
    first = outbox[0][1]
    c.post("/api/auth/request-code", json={"email": EMAIL})
    if first != outbox[1][1]:
        assert c.post("/api/auth/verify", json={"email": EMAIL, "code": first}).status_code == 400


# ---------- الجلسات والحسابات ----------

def test_login_sets_session_and_rotates_anon_cookie(outbox):
    c = TestClient(main.app)
    c.get("/")
    anon_before = c.cookies[COOKIE_NAME]
    r = _login(c, outbox)
    assert r.json()["email"] == EMAIL
    assert SESSION_COOKIE in c.cookies and c.cookies[COOKIE_NAME] != anon_before
    assert c.get("/api/me").json()["user"] == {"email": EMAIL}

    # المعرّف القديم (الذي أصبح حسابًا) لا يفتح الحساب عبر الكوكي المجهول
    stolen = TestClient(main.app, cookies={COOKIE_NAME: anon_before})
    assert stolen.get("/api/me").json()["user"] is None


def test_same_account_on_second_device(outbox, monkeypatch, summarize_stub):
    _allow_resend(monkeypatch)
    phone = TestClient(main.app)
    _login(phone, outbox)
    phone.post("/api/summarize", json={"url": URL})
    db.activate_pro(db.find_by_email(EMAIL), "cus_1", "sub_1")

    laptop = TestClient(main.app)
    _login(laptop, outbox)
    me = laptop.get("/api/me").json()
    assert me["user"] == {"email": EMAIL} and me["usage"]["plan"] == "pro"


def test_login_does_not_reset_quota(outbox, monkeypatch, summarize_stub):
    _allow_resend(monkeypatch)
    a = TestClient(main.app)
    _login(a, outbox)
    for _ in range(3):
        a.post("/api/summarize", json={"url": URL})
    assert a.post("/api/summarize", json={"url": URL}).status_code == 402

    b = TestClient(main.app)  # متصفح جديد بحصة مجهولة كاملة
    b.post("/api/summarize", json={"url": URL})
    _login(b, outbox)
    assert b.get("/api/me").json()["usage"]["remaining"] == 0


def test_anonymous_pro_is_merged_into_account(outbox, monkeypatch):
    _allow_resend(monkeypatch)
    existing = TestClient(main.app)
    _login(existing, outbox)
    account = db.find_by_email(EMAIL)

    anon = TestClient(main.app)
    anon.get("/")
    anon_id = anon.cookies[COOKIE_NAME]
    db.activate_pro(anon_id, "cus_9", "sub_9")
    _login(anon, outbox)

    assert db.get_usage(account).is_pro and not db.get_usage(anon_id).is_pro
    # أحداث Stripe اللاحقة لهذا الاشتراك تصل للحساب
    db.set_plan_by_subscription("sub_9", db.PLAN_FREE)
    assert not db.get_usage(account).is_pro


def test_switching_accounts_creates_new_account(outbox, monkeypatch):
    _allow_resend(monkeypatch)
    c = TestClient(main.app)
    _login(c, outbox)
    first = db.find_by_email(EMAIL)
    _login(c, outbox, "other@example.com")
    second = db.find_by_email("other@example.com")
    assert second and second != first
    assert c.get("/api/me").json()["user"] == {"email": "other@example.com"}


def test_logout(outbox):
    c = TestClient(main.app)
    _login(c, outbox)
    token = c.cookies[SESSION_COOKIE]
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/me").json()["user"] is None
    # الجلسة حُذفت من الخادم: إعادة استخدام الرمز القديم لا تنفع
    replay = TestClient(main.app, cookies={SESSION_COOKIE: token})
    assert replay.get("/api/me").json()["user"] is None


def test_expired_session(outbox, monkeypatch):
    c = TestClient(main.app)
    _login(c, outbox)
    monkeypatch.setattr(settings, "session_days", 0)
    real = time.time
    import app.visitors as visitors
    monkeypatch.setattr(visitors.time, "time", lambda: real() + 31 * 86400)
    assert c.get("/api/me").json()["user"] is None


# ---------- الربط مع الدفع والحصة ----------

def test_payment_requires_login_when_enabled(outbox, monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(settings, "stripe_price_id", "price_1")
    for k, v in dict(paypal_client_id="c", paypal_client_secret="s", paypal_plan_id="P-1").items():
        monkeypatch.setattr(settings, k, v)
    c = TestClient(main.app)
    assert c.post("/api/checkout/session").status_code == 401
    assert c.post("/api/paypal/subscription").status_code == 401


def test_stripe_checkout_gets_account_email(outbox, monkeypatch):
    from app import billing
    import stripe
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(settings, "stripe_price_id", "price_1")
    captured = {}

    class Sessions:
        def create(self, params):
            captured.update(params)
            return stripe.checkout.Session.construct_from({"id": "cs_1", "url": "https://x"}, "k")

    class Client:
        class v1:
            class checkout:
                sessions = Sessions()

    monkeypatch.setattr(billing, "_client", lambda: Client)
    c = TestClient(main.app)
    _login(c, outbox)
    assert c.post("/api/checkout/session").status_code == 200
    assert captured["customer_email"] == EMAIL
    assert captured["client_reference_id"] == db.find_by_email(EMAIL)


def test_require_login_blocks_anonymous_summaries(outbox, monkeypatch, summarize_stub):
    monkeypatch.setattr(settings, "require_login", True)
    c = TestClient(main.app)
    assert c.get("/api/me").json()["require_login"] is True
    assert c.post("/api/summarize", json={"url": URL}).status_code == 401
    assert c.get("/api/me").json()["usage"]["used"] == 0
    _login(c, outbox)
    assert c.post("/api/summarize", json={"url": URL}).status_code == 200


def test_old_database_gets_email_column(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE visitors (id TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0,
        plan TEXT NOT NULL DEFAULT 'free', stripe_customer_id TEXT, stripe_subscription_id TEXT,
        paypal_subscription_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT INTO visitors (id, used, plan) VALUES ('old', 1, 'pro')")
    conn.commit(); conn.close()
    monkeypatch.setattr(settings, "database_path", str(path))
    db.claim_email("old", EMAIL)
    assert db.find_by_email(EMAIL) == "old" and db.get_usage("old").is_pro
