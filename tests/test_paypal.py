import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, main, paypal
from app.config import settings
from app.visitors import COOKIE_NAME

SUB_ID = "I-ABC123DEF456"
PLAN = "P-PLAN123"


@pytest.fixture
def paypal_on(monkeypatch):
    monkeypatch.setattr(settings, "paypal_client_id", "client-id-123")
    monkeypatch.setattr(settings, "paypal_client_secret", "secret-xyz")
    monkeypatch.setattr(settings, "paypal_plan_id", PLAN)
    monkeypatch.setattr(settings, "paypal_webhook_id", "WH-1")
    paypal._token.update(value=None, expires_at=0)


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.content = b"x"
        self.text = str(body)

    def json(self):
        return self._body


@pytest.fixture
def fake_paypal(monkeypatch, paypal_on):
    """يحاكي واجهة PayPal REST ويسجّل الطلبات."""
    state = {"calls": [], "subscription": None, "verify": "SUCCESS", "oauth": 0}

    def post(url, auth=None, data=None, timeout=None):
        state["oauth"] += 1
        assert url == "https://api-m.sandbox.paypal.com/v1/oauth2/token"
        assert auth == ("client-id-123", "secret-xyz")
        return FakeResponse(200, {"access_token": "tok", "expires_in": 3600})

    def request(method, url, headers=None, json=None, timeout=None):
        state["calls"].append((method, url, json))
        assert headers["Authorization"] == "Bearer tok"
        if url.endswith("/v1/billing/subscriptions") and method == "POST":
            return FakeResponse(201, {"id": SUB_ID, "status": "APPROVAL_PENDING"})
        if "/v1/billing/subscriptions/" in url:
            return FakeResponse(200, state["subscription"])
        if url.endswith("/verify-webhook-signature"):
            return FakeResponse(200, {"verification_status": state["verify"]})
        return FakeResponse(404, {})

    monkeypatch.setattr(paypal.requests, "post", post)
    monkeypatch.setattr(paypal.requests, "request", request)
    return state


def _client():
    c = TestClient(main.app)
    c.get("/")
    return c, c.cookies[COOKIE_NAME]


# ---------- الإعدادات والواجهة ----------

def test_disabled_without_credentials():
    assert TestClient(main.app).post("/api/paypal/subscription").status_code == 503
    assert TestClient(main.app).get("/api/me").json()["paypal"] == {
        "enabled": False, "client_id": None, "currency": "USD"}


def test_client_id_exposed_secret_never(paypal_on):
    c = TestClient(main.app)
    me = c.get("/api/me").json()
    assert me["paypal"] == {"enabled": True, "client_id": "client-id-123", "currency": "USD"}
    page = c.get("/").text
    assert "client-id-123" in page and "secret-xyz" not in page and 'id="paypal-buttons"' in page


def test_api_base_follows_env(monkeypatch):
    monkeypatch.setattr(settings, "paypal_env", "live")
    assert settings.paypal_api_base == "https://api-m.paypal.com"
    monkeypatch.setattr(settings, "paypal_env", "sandbox")
    assert settings.paypal_api_base == "https://api-m.sandbox.paypal.com"


# ---------- إنشاء الاشتراك ----------

def test_create_subscription_links_visitor(fake_paypal):
    c, vid = _client()
    assert c.post("/api/paypal/subscription").json() == {"id": SUB_ID}
    method, url, body = fake_paypal["calls"][0]
    assert (method, body["plan_id"], body["custom_id"]) == ("POST", PLAN, vid)
    assert body["application_context"]["shipping_preference"] == "NO_SHIPPING"


def test_access_token_is_cached(fake_paypal):
    c, _ = _client()
    c.post("/api/paypal/subscription")
    c.post("/api/paypal/subscription")
    assert fake_paypal["oauth"] == 1


# ---------- صفحة النجاح /checkout ----------

def _sub(**over):
    return {"id": SUB_ID, "status": "ACTIVE", "plan_id": PLAN, **over}


def test_checkout_paypal_success_activates(fake_paypal):
    c, vid = _client()
    fake_paypal["subscription"] = _sub(custom_id=vid)
    r = c.get("/checkout", params={"provider": "paypal", "subscription_id": SUB_ID})
    assert r.status_code == 200 and "تم تفعيل اشتراكك" in r.text
    assert db.get_usage(vid).is_pro


def test_checkout_paypal_link_in_other_browser_does_not_grant_account(fake_paypal):
    _, payer_vid = _client()
    fake_paypal["subscription"] = _sub(custom_id=payer_vid)
    other, _ = _client()
    r = other.get("/checkout", params={"provider": "paypal", "subscription_id": SUB_ID})
    assert "سجّل الدخول" in r.text and db.get_usage(payer_vid).is_pro
    assert other.get("/api/me").json()["usage"]["plan"] == "free"


@pytest.mark.parametrize(
    "sub,code",
    [(_sub(custom_id="c" * 32, plan_id="P-OTHER"), 400),        # خطة مختلفة
     (_sub(custom_id="c" * 32, status="APPROVAL_PENDING"), 200),  # قيد المعالجة
     (_sub(custom_id="c" * 32, status="CANCELLED"), 400)],
)
def test_checkout_paypal_inactive_or_wrong_plan(fake_paypal, sub, code):
    fake_paypal["subscription"] = sub
    r = TestClient(main.app).get("/checkout", params={"provider": "paypal", "subscription_id": SUB_ID})
    assert r.status_code == code
    assert not db.get_usage("c" * 32).is_pro


@pytest.mark.parametrize("bad", ["", "../x", "I-abc", "P-ABC123DEF"])
def test_checkout_paypal_rejects_bad_id(fake_paypal, bad):
    r = TestClient(main.app).get("/checkout", params={"provider": "paypal", "subscription_id": bad})
    assert r.status_code == 400 and fake_paypal["calls"] == []


# ---------- الـ Webhook ----------

HEADERS = {f"paypal-{n}": "v" for n in
           ["auth-algo", "cert-url", "transmission-id", "transmission-sig", "transmission-time"]}


def _event(type_, resource):
    return {"id": "WH-EVT", "event_type": type_, "resource": resource}


def test_webhook_activates_then_cancels(fake_paypal):
    c = TestClient(main.app)
    vid = "d" * 32
    r = c.post("/api/paypal/webhook", json=_event("BILLING.SUBSCRIPTION.ACTIVATED", _sub(custom_id=vid)), headers=HEADERS)
    assert r.status_code == 200 and db.get_usage(vid).is_pro
    verify_body = fake_paypal["calls"][0][2]
    assert verify_body["webhook_id"] == "WH-1" and verify_body["transmission_id"] == "v"

    r = c.post("/api/paypal/webhook", json=_event("BILLING.SUBSCRIPTION.CANCELLED", {"id": SUB_ID}), headers=HEADERS)
    assert r.status_code == 200 and not db.get_usage(vid).is_pro


def test_webhook_rejects_unverified(fake_paypal):
    fake_paypal["verify"] = "FAILURE"
    r = TestClient(main.app).post(
        "/api/paypal/webhook", json=_event("BILLING.SUBSCRIPTION.ACTIVATED", _sub(custom_id="e" * 32)), headers=HEADERS)
    assert r.status_code == 400 and not db.get_usage("e" * 32).is_pro


def test_webhook_rejects_missing_headers(fake_paypal):
    r = TestClient(main.app).post("/api/paypal/webhook", json=_event("x", {}))
    assert r.status_code == 400 and fake_paypal["calls"] == []


# ---------- ترحيل قاعدة البيانات ----------

def test_old_database_gets_paypal_column(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE visitors (id TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0,
        plan TEXT NOT NULL DEFAULT 'free', stripe_customer_id TEXT, stripe_subscription_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT INTO visitors (id, used) VALUES ('old', 2)")
    conn.commit(); conn.close()

    monkeypatch.setattr(settings, "database_path", str(path))
    db.activate_pro_paypal("old", SUB_ID)
    usage = db.get_usage("old")
    assert usage.is_pro and usage.used == 2
