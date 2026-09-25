import json
import time

import pytest
import stripe
from fastapi.testclient import TestClient

from app import billing, db, main
from app.config import settings
from app.visitors import COOKIE_NAME


@pytest.fixture
def stripe_on(monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_123")
    monkeypatch.setattr(settings, "stripe_price_id", "price_123")
    monkeypatch.setattr(settings, "stripe_webhook_secret", "whsec_test")


class FakeSessions:
    def __init__(self, session=None):
        self.created = None
        self.session = session

    def create(self, params):
        self.created = params
        return stripe.checkout.Session.construct_from({"id": "cs_1", "url": "https://checkout.stripe.com/c/cs_1"}, "k")

    def retrieve(self, session_id):
        return self.session


def _fake_client(monkeypatch, sessions):
    class Client:
        class v1:
            class checkout:
                pass

    Client.v1.checkout.sessions = sessions
    monkeypatch.setattr(billing, "_client", lambda: Client)


def _session(**fields):
    base = {"id": "cs_1", "status": "complete", "payment_status": "paid",
            "customer": "cus_1", "subscription": "sub_1"}
    return stripe.checkout.Session.construct_from({**base, **fields}, "k")


def test_checkout_disabled_without_keys():
    r = TestClient(main.app).post("/api/checkout/session")
    assert r.status_code == 503


def test_create_checkout_session(stripe_on, monkeypatch):
    sessions = FakeSessions()
    _fake_client(monkeypatch, sessions)
    c = TestClient(main.app)
    c.get("/")
    r = c.post("/api/checkout/session")
    assert r.json() == {"url": "https://checkout.stripe.com/c/cs_1"}
    p = sessions.created
    assert p["mode"] == "subscription"
    assert p["line_items"] == [{"price": "price_123", "quantity": 1}]
    assert p["client_reference_id"] == c.cookies[COOKIE_NAME]
    assert p["success_url"] == "http://yt2x.example/checkout?session_id={CHECKOUT_SESSION_ID}"


def test_checkout_success_activates_pro(stripe_on, monkeypatch):
    c = TestClient(main.app)
    c.get("/")
    vid = c.cookies[COOKIE_NAME]
    _fake_client(monkeypatch, FakeSessions(_session(client_reference_id=vid)))

    r = c.get("/checkout", params={"session_id": "cs_1"})
    assert r.status_code == 200 and "تم تفعيل اشتراكك" in r.text
    assert db.get_usage(vid).is_pro


def test_checkout_link_in_other_browser_does_not_grant_account(stripe_on, monkeypatch):
    payer = TestClient(main.app)
    payer.get("/")
    paid_vid = payer.cookies[COOKIE_NAME]
    _fake_client(monkeypatch, FakeSessions(_session(client_reference_id=paid_vid)))

    other = TestClient(main.app)
    other.get("/")
    r = other.get("/checkout", params={"session_id": "cs_1"})
    assert r.status_code == 200 and "سجّل الدخول" in r.text
    assert db.get_usage(paid_vid).is_pro
    assert other.cookies[COOKIE_NAME] != paid_vid
    assert other.get("/api/me").json()["usage"]["plan"] == "free"


@pytest.mark.parametrize(
    "session,expected",
    [(_session(client_reference_id="a" * 32, status="open", payment_status="unpaid"), 200),
     (_session(client_reference_id="a" * 32, payment_status="unpaid"), 400)],
)
def test_unpaid_session_does_not_activate(stripe_on, monkeypatch, session, expected):
    _fake_client(monkeypatch, FakeSessions(session))
    r = TestClient(main.app).get("/checkout", params={"session_id": "cs_1"})
    assert r.status_code == expected
    assert not db.get_usage("a" * 32).is_pro


def test_checkout_rejects_bad_session_id(stripe_on):
    assert TestClient(main.app).get("/checkout", params={"session_id": "nope"}).status_code == 400


def _signed(payload: dict, secret="whsec_test"):
    body = json.dumps(payload)
    ts = int(time.time())
    sig = stripe.WebhookSignature._compute_signature(f"{ts}.{body}", secret)
    return body, {"stripe-signature": f"t={ts},v1={sig}", "content-type": "application/json"}


def _event(type_, obj):
    return {"id": "evt_1", "object": "event", "type": type_, "data": {"object": obj}}


def test_webhook_rejects_bad_signature(stripe_on):
    body, headers = _signed(_event("x", {"id": "o"}), secret="whsec_wrong")
    assert TestClient(main.app).post("/api/stripe/webhook", content=body, headers=headers).status_code == 400


def test_webhook_activates_and_cancels(stripe_on):
    vid = "b" * 32
    c = TestClient(main.app)
    session = {"id": "cs_1", "object": "checkout.session", "status": "complete",
               "payment_status": "paid", "client_reference_id": vid,
               "customer": "cus_1", "subscription": "sub_9"}
    body, headers = _signed(_event("checkout.session.completed", session))
    assert c.post("/api/stripe/webhook", content=body, headers=headers).status_code == 200
    assert db.get_usage(vid).is_pro

    sub = {"id": "sub_9", "object": "subscription", "status": "canceled"}
    body, headers = _signed(_event("customer.subscription.deleted", sub))
    assert c.post("/api/stripe/webhook", content=body, headers=headers).status_code == 200
    assert not db.get_usage(vid).is_pro
