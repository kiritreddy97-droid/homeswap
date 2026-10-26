"""End-to-end API tests through the real FastAPI app (SQLite in memory, OTP sender captured)."""

import hashlib
import hmac
import json
from datetime import timedelta

import jwt
import pytest

from app import main, services
from app.config import DEV_JWT_SECRET
from app.timeutil import today

START = today() + timedelta(days=10)
END = START + timedelta(days=2)


def swap_body(home_id, start=START, end=END):
    return {"home_id": home_id, "start": start.isoformat(), "end": end.isoformat()}


@pytest.fixture
def world(client, make_member, admin):
    """Host with a verified home (200 bonus points), a verified guest with 200 points of their own."""
    host = make_member("asha@example.in", "Asha")
    guest = make_member("ravi@example.in", "Ravi")
    home_id = host.post("/homes", {"city": "Goa", "title": "Beach flat", "points_per_night": 100}).json()["id"]
    assert admin.post(f"/admin/homes/{home_id}/verify").status_code == 200
    guest_home = guest.post("/homes", {"city": "Mumbai", "title": "Bandra 2BHK"}).json()["id"]
    assert admin.post(f"/admin/homes/{guest_home}/verify").status_code == 200
    return {"host": host, "guest": guest, "home_id": home_id}


@pytest.fixture
def completed_swap(client, world, admin, monkeypatch):
    host, guest = world["host"], world["guest"]
    swap_id = guest.post("/swaps", swap_body(world["home_id"])).json()["id"]
    assert host.post(f"/swaps/{swap_id}/approve").status_code == 200
    monkeypatch.setattr(services, "today", lambda: END + timedelta(days=1))
    assert guest.post(f"/swaps/{swap_id}/complete").json() == {"status": "completed"}
    return swap_id


# ---------- health & auth ----------


def test_health_is_public_and_sets_request_id(client):
    r = client.get("/health")
    assert r.json() == {"status": "ok"} and r.headers["x-request-id"]
    assert client.get("/health", headers={"X-Request-ID": "abc"}).headers["x-request-id"] == "abc"


def test_openapi_lists_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/health", "/auth/otp/request", "/swaps", "/kyc/webhook", "/admin/damage-claims"} <= set(paths)


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/me"),
        ("get", "/homes"),
        ("post", "/homes"),
        ("post", "/swaps"),
        ("get", "/swaps/1"),
        ("post", "/swaps/1/approve"),
        ("get", "/users/1/balance"),
        ("post", "/admin/users/1/verify"),
        ("post", "/admin/homes/1/verify"),
        ("get", "/admin/damage-claims"),
    ],
)
def test_protected_routes_reject_anonymous(client, method, url):
    r = getattr(client, method)(url)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_login_creates_user_and_returns_working_token(client, sender):
    assert client.post("/auth/otp/request", json={"identifier": "Priya@Example.in "}).status_code == 202
    code = sender.codes["priya@example.in"]  # normalised
    r = client.post("/auth/otp/verify", json={"identifier": "priya@example.in", "code": code, "name": "Priya"})
    body = r.json()
    assert body["user"]["role"] == "member" and body["user"]["name"] == "Priya"
    me = client.get("/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["email"] == "priya@example.in"


def test_phone_login_normalises_indian_numbers(client, sender):
    client.post("/auth/otp/request", json={"identifier": "98765 43210"})
    assert "+919876543210" in sender.codes
    r = client.post("/auth/otp/verify", json={"identifier": "+919876543210", "code": sender.codes["+919876543210"]})
    assert r.json()["user"]["phone"] == "+919876543210"


def test_second_login_returns_same_user(client, make_member):
    assert make_member("same@example.in", "A", verified=False).id == make_member("same@example.in", "A").id


def test_invalid_identifier_rejected(client):
    assert client.post("/auth/otp/request", json={"identifier": "not a contact"}).status_code == 422


def test_wrong_code_rejected_and_attempts_are_limited(client, sender):
    client.post("/auth/otp/request", json={"identifier": "x@example.in"})
    good = sender.codes["x@example.in"]
    bad = "000000" if good != "000000" else "111111"
    for _ in range(5):
        assert client.post("/auth/otp/verify", json={"identifier": "x@example.in", "code": bad}).status_code == 401
    # attempts are exhausted: even the right code no longer works
    assert client.post("/auth/otp/verify", json={"identifier": "x@example.in", "code": good}).status_code == 401


def test_otp_is_single_use(client, sender):
    client.post("/auth/otp/request", json={"identifier": "y@example.in"})
    payload = {"identifier": "y@example.in", "code": sender.codes["y@example.in"]}
    assert client.post("/auth/otp/verify", json=payload).status_code == 200
    assert client.post("/auth/otp/verify", json=payload).status_code == 401


def test_expired_otp_rejected(client, sender, monkeypatch):
    client.post("/auth/otp/request", json={"identifier": "z@example.in"})
    from app import auth

    monkeypatch.setattr(auth, "now", lambda: __import__("app.timeutil", fromlist=["now"]).now() + timedelta(hours=1))
    r = client.post("/auth/otp/verify", json={"identifier": "z@example.in", "code": sender.codes["z@example.in"]})
    assert r.status_code == 401


def test_otp_request_throttled_per_identifier(client):
    codes = [client.post("/auth/otp/request", json={"identifier": "t@example.in"}).status_code for _ in range(4)]
    assert codes == [202, 202, 202, 429]


def test_bad_tokens_rejected(client, make_member):
    m = make_member()
    hdr = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    assert client.get("/me", headers=hdr("garbage")).status_code == 401
    forged = jwt.encode(
        {"sub": str(m.id), "exp": 9_999_999_999}, "wrong-secret-that-is-long-enough-32b", algorithm="HS256"
    )
    assert client.get("/me", headers=hdr(forged)).status_code == 401
    expired = jwt.encode({"sub": str(m.id), "exp": 1}, DEV_JWT_SECRET, algorithm="HS256")
    assert client.get("/me", headers=hdr(expired)).status_code == 401
    unsigned = jwt.encode({"sub": str(m.id), "exp": 9_999_999_999}, None, algorithm="none")
    assert client.get("/me", headers=hdr(unsigned)).status_code == 401
    no_sub = jwt.encode({"exp": 9_999_999_999}, DEV_JWT_SECRET, algorithm="HS256")
    assert client.get("/me", headers=hdr(no_sub)).status_code == 401
    assert client.get("/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_token_for_deleted_user_rejected(client, make_member):
    m = make_member()
    with client.session_factory() as s:
        from app.models import User

        s.delete(s.get(User, m.id))
        s.commit()
    assert m.get("/me").status_code == 401


def test_member_cannot_use_admin_routes_or_self_promote(client, make_member, admin):
    m = make_member("m@example.in", "M", verified=False)
    assert m.post(f"/admin/users/{m.id}/verify").status_code == 403
    assert m.post("/admin/homes/1/verify").status_code == 403
    assert m.get("/admin/damage-claims").status_code == 403
    assert m.get("/me").json()["role"] == "member"
    assert admin.post(f"/admin/users/{m.id}/verify").status_code == 200
    assert m.get("/me").json()["id_verified"] is True


def test_demoted_admin_loses_access_immediately(client, admin):
    assert admin.get("/admin/damage-claims").status_code == 200
    with client.session_factory() as s:
        from app.models import User

        s.get(User, admin.id).role = "member"
        s.commit()
    assert admin.get("/admin/damage-claims").status_code == 403


def test_profile_update_and_validation(client, make_member):
    m = make_member()
    assert m.client.patch("/me", headers=m.headers, json={"name": "  Asha K "}).json()["name"] == "Asha K"
    assert m.client.patch("/me", headers=m.headers, json={"name": ""}).status_code == 422


# ---------- KYC webhook ----------


def _signed(secret, payload):
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return {"content": raw, "headers": {"X-Signature": sig, "Content-Type": "application/json"}}


def test_kyc_webhook_disabled_without_secret(client, make_member):
    m = make_member(verified=False)
    assert client.post("/kyc/webhook", **_signed("x", {"user_id": m.id, "status": "verified"})).status_code == 503


def test_kyc_webhook_requires_valid_signature(client, make_member, monkeypatch):
    monkeypatch.setenv("KYC_WEBHOOK_SECRET", "s3cret")
    m = make_member(verified=False)
    event = {"user_id": m.id, "status": "verified"}
    assert client.post("/kyc/webhook", **_signed("wrong", event)).status_code == 401
    assert client.post("/kyc/webhook", json=event).status_code == 401  # unsigned
    assert m.get("/me").json()["id_verified"] is False
    assert client.post("/kyc/webhook", **_signed("s3cret", event)).json() == {"id_verified": True}
    assert m.get("/me").json()["id_verified"] is True
    assert client.post("/kyc/webhook", **_signed("s3cret", {"user_id": m.id, "status": "rejected"})).status_code == 200
    assert m.get("/me").json()["membership_active"] is False


def test_kyc_webhook_validates_payload_and_user(client, monkeypatch):
    monkeypatch.setenv("KYC_WEBHOOK_SECRET", "s3cret")
    assert client.post("/kyc/webhook", **_signed("s3cret", {"user_id": 1, "status": "maybe"})).status_code == 422
    assert client.post("/kyc/webhook", **_signed("s3cret", {"user_id": 99, "status": "verified"})).status_code == 404


# ---------- homes ----------


def test_home_validation_and_listing(client, world, make_member):
    host, guest = world["host"], world["guest"]
    assert host.post("/homes", {"city": "", "title": "x"}).status_code == 422
    assert host.post("/homes", {"city": "Goa", "title": "x", "points_per_night": 0}).status_code == 422
    host.post("/homes", {"city": "Goa", "title": "unverified"})  # not listed until an admin verifies it
    titles = [h["title"] for h in guest.get("/homes", params={"city": "goa"}).json()]
    assert titles == ["Beach flat"]


def test_home_owner_comes_from_token_not_body(client, world):
    # an attacker-supplied owner_id is ignored
    hid = world["guest"].post("/homes", {"city": "Pune", "title": "t", "owner_id": world["host"].id}).json()["id"]
    with client.session_factory() as s:
        from app.models import Home

        assert s.get(Home, hid).owner_id == world["guest"].id


# ---------- swaps ----------


def test_full_swap_flow_moves_points(client, world):
    host, guest = world["host"], world["guest"]
    assert host.get("/me/balance").json() == {"points": 200}
    swap = guest.post("/swaps", swap_body(world["home_id"]))
    assert swap.status_code == 201 and swap.json()["points_cost"] == 200
    swap_id = swap.json()["id"]
    assert host.post(f"/swaps/{swap_id}/approve").json() == {"status": "approved"}
    assert guest.get("/me/balance").json() == {"points": 0}
    assert host.get("/me/balance").json() == {"points": 400}
    assert [s["id"] for s in guest.get("/me/swaps").json()] == [swap_id]
    assert [s["id"] for s in host.get("/me/swaps").json()] == [swap_id]
    assert guest.post(f"/swaps/{swap_id}/cancel").json() == {"status": "cancelled"}
    assert guest.get("/me/balance").json() == {"points": 200}
    assert host.get("/me/balance").json() == {"points": 200}


def test_swap_business_rule_errors_are_409(client, world, make_member):
    host, guest, home_id = world["host"], world["guest"], world["home_id"]
    assert host.post("/swaps", swap_body(home_id)).json()["detail"] == "cannot stay in your own home"
    unverified = make_member("u@example.in", "U", verified=False)
    assert unverified.post("/swaps", swap_body(home_id)).json()["detail"] == "guest identity not verified"
    assert guest.post("/swaps", swap_body(home_id, END, START)).status_code == 409
    long_stay = swap_body(home_id, START, START + timedelta(days=5))
    assert guest.post("/swaps", long_stay).json()["detail"] == "insufficient points"
    past = swap_body(home_id, today() - timedelta(days=3), today() - timedelta(days=1))
    assert guest.post("/swaps", past).json()["detail"] == "stay cannot start in the past"
    assert guest.post("/swaps", swap_body(9999)).status_code == 404


def test_swap_access_control(client, world, make_member, admin):
    host, guest, home_id = world["host"], world["guest"], world["home_id"]
    swap_id = guest.post("/swaps", swap_body(home_id)).json()["id"]
    stranger = make_member("s@example.in", "S")
    assert guest.post(f"/swaps/{swap_id}/approve").status_code == 404  # guest cannot approve own request
    for action in ("approve", "cancel", "complete"):
        assert stranger.post(f"/swaps/{swap_id}/{action}").status_code == 404
    assert stranger.get(f"/swaps/{swap_id}").status_code == 404
    assert host.get(f"/swaps/{swap_id}").status_code == 200
    assert guest.get(f"/swaps/{swap_id}").status_code == 200
    assert admin.get(f"/swaps/{swap_id}").status_code == 200
    assert host.post(f"/swaps/{swap_id}/approve").status_code == 200
    assert host.post(f"/swaps/{swap_id}/approve").status_code == 409  # not twice


def test_balance_visible_only_to_self_or_admin(client, world, admin):
    host, guest = world["host"], world["guest"]
    assert guest.get(f"/users/{guest.id}/balance").json() == {"points": 200}
    assert guest.get(f"/users/{host.id}/balance").status_code == 404
    assert admin.get(f"/users/{host.id}/balance").json() == {"points": 200}


def test_cannot_complete_before_checkout(client, world):
    swap_id = world["guest"].post("/swaps", swap_body(world["home_id"])).json()["id"]
    world["host"].post(f"/swaps/{swap_id}/approve")
    r = world["guest"].post(f"/swaps/{swap_id}/complete")
    assert r.status_code == 409 and r.json()["detail"] == "stay has not ended yet"


# ---------- reviews ----------


def test_reviews_require_completed_stay(client, world):
    swap_id = world["guest"].post("/swaps", swap_body(world["home_id"])).json()["id"]
    r = world["guest"].post(f"/swaps/{swap_id}/reviews", {"rating": 5})
    assert r.status_code == 409 and "completed" in r.json()["detail"]


def test_reviews_flow(client, world, completed_swap, make_member, admin):
    host, guest, swap_id = world["host"], world["guest"], completed_swap
    r = guest.post(f"/swaps/{swap_id}/reviews", {"rating": 5, "comment": "Lovely"})
    assert r.status_code == 201 and r.json()["reviewee_id"] == host.id
    assert guest.post(f"/swaps/{swap_id}/reviews", {"rating": 4}).status_code == 409  # only once
    assert host.post(f"/swaps/{swap_id}/reviews", {"rating": 3}).json()["reviewee_id"] == guest.id
    assert guest.post(f"/swaps/{swap_id}/reviews", {"rating": 9}).status_code == 422
    assert make_member("o@example.in", "O").post(f"/swaps/{swap_id}/reviews", {"rating": 1}).status_code == 404
    assert admin.post(f"/swaps/{swap_id}/reviews", {"rating": 1}).status_code == 404

    home = guest.get(f"/homes/{world['home_id']}/reviews").json()
    assert home["count"] == 1 and home["average_rating"] == 5
    assert guest.get(f"/users/{guest.id}/reviews").json()["average_rating"] == 3
    assert guest.get("/users/9999/reviews").status_code == 404


# ---------- damage claims ----------


def test_damage_claim_flow(client, world, completed_swap, admin):
    host, guest, swap_id = world["host"], world["guest"], completed_swap
    claim_body = {"description": "Broken TV", "amount": 12000}
    assert guest.post(f"/swaps/{swap_id}/damage-claim", claim_body).status_code == 409  # guests cannot claim
    r = host.post(f"/swaps/{swap_id}/damage-claim", claim_body)
    assert r.status_code == 201 and r.json()["status"] == "open"
    claim_id = r.json()["id"]
    assert host.post(f"/swaps/{swap_id}/damage-claim", claim_body).status_code == 409  # one per stay
    assert guest.get(f"/damage-claims/{claim_id}").status_code == 200
    assert [c["id"] for c in admin.get("/admin/damage-claims", params={"status": "open"}).json()] == [claim_id]
    assert host.post(f"/admin/damage-claims/{claim_id}/resolve", {"approve": True}).status_code == 403
    too_much = admin.post(f"/admin/damage-claims/{claim_id}/resolve", {"approve": True, "amount_awarded": 99999})
    assert too_much.status_code == 409
    done = admin.post(
        f"/admin/damage-claims/{claim_id}/resolve", {"approve": True, "amount_awarded": 8000, "note": "ok"}
    )
    assert done.json()["status"] == "approved" and done.json()["amount_awarded"] == 8000
    assert admin.post(f"/admin/damage-claims/{claim_id}/resolve", {"approve": False}).status_code == 409
    assert admin.get("/admin/damage-claims", params={"status": "open"}).json() == []


def test_damage_claim_rejection_awards_nothing(client, world, completed_swap, admin):
    claim_id = (
        world["host"].post(f"/swaps/{completed_swap}/damage-claim", {"description": "x", "amount": 5}).json()["id"]
    )
    r = admin.post(f"/admin/damage-claims/{claim_id}/resolve", {"approve": False, "note": "no evidence"})
    assert r.json()["status"] == "rejected" and r.json()["amount_awarded"] == 0


def test_damage_claim_access_and_windows(client, world, completed_swap, make_member, monkeypatch):
    host, swap_id = world["host"], completed_swap
    assert (
        make_member("o@example.in", "O")
        .post(f"/swaps/{swap_id}/damage-claim", {"description": "x", "amount": 1})
        .status_code
        == 404
    )
    assert host.post(f"/swaps/{swap_id}/damage-claim", {"description": "x", "amount": -1}).status_code == 422
    monkeypatch.setattr(services, "today", lambda: END + timedelta(days=services.CLAIM_WINDOW_DAYS + 1))
    late = host.post(f"/swaps/{swap_id}/damage-claim", {"description": "x", "amount": 1})
    assert late.status_code == 409 and "within" in late.json()["detail"]


def test_damage_claim_needs_completed_stay(client, world):
    swap_id = world["guest"].post("/swaps", swap_body(world["home_id"])).json()["id"]
    r = world["host"].post(f"/swaps/{swap_id}/damage-claim", {"description": "x", "amount": 1})
    assert r.status_code == 409


def test_claim_hidden_from_strangers(client, world, completed_swap, make_member):
    claim_id = (
        world["host"].post(f"/swaps/{completed_swap}/damage-claim", {"description": "x", "amount": 1}).json()["id"]
    )
    assert make_member("o@example.in", "O").get(f"/damage-claims/{claim_id}").status_code == 404


# ---------- rate limiting & logging ----------


def test_rate_limit_returns_429_with_retry_after(client, make_member):
    m = make_member()
    main.limiter.hits.clear()
    old = main.limiter.per_minute
    main.limiter.per_minute = 3
    try:
        statuses = [m.get("/me").status_code for _ in range(5)]
        assert statuses == [200, 200, 200, 429, 429]
        limited = m.get("/me")
        assert int(limited.headers["retry-after"]) >= 1 and limited.headers["x-request-id"]
        assert client.get("/health").status_code == 200  # health checks are never limited
    finally:
        main.limiter.per_minute = old


def test_auth_endpoints_have_their_own_stricter_limit(client):
    old = main.auth_limiter.per_minute
    main.auth_limiter.per_minute = 2
    try:
        codes = [
            client.post("/auth/otp/request", json={"identifier": f"u{i}@example.in"}).status_code for i in range(3)
        ]
        assert codes == [202, 202, 429]
        assert client.get("/health").status_code == 200
    finally:
        main.auth_limiter.per_minute = old


def test_access_log_is_structured_json(client, caplog):
    import logging

    from app.middleware import JsonFormatter

    with caplog.at_level(logging.INFO, logger="homeswap.request"):
        client.get("/health", headers={"X-Request-ID": "req-1"})
    record = next(r for r in caplog.records if r.name == "homeswap.request")
    line = json.loads(JsonFormatter().format(record))
    assert line["request_id"] == "req-1" and line["path"] == "/health" and line["status"] == 200
    assert line["method"] == "GET" and "duration_ms" in line


def test_unhandled_error_is_500_json_and_logged(client, caplog, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(main.auth, "issue_otp", boom)
    r = client.post("/auth/otp/request", json={"identifier": "e@example.in"})
    assert r.status_code == 500 and r.json() == {"detail": "internal server error"}
    assert any("unhandled error" in rec.getMessage() for rec in caplog.records)


# ---------- web client support ----------


def test_my_homes_and_admin_lists(client, world, admin, make_member):
    host = world["host"]
    pending_id = host.post("/homes", {"city": "Goa", "title": "Pending"}).json()["id"]
    mine = host.get("/me/homes").json()
    assert [(h["title"], h["property_verified"]) for h in mine] == [("Beach flat", True), ("Pending", False)]
    assert world["guest"].get("/me/homes").json()[0]["title"] == "Bandra 2BHK"
    assert [h["id"] for h in admin.get("/admin/homes", params={"verified": "false"}).json()] == [pending_id]
    assert len(admin.get("/admin/homes").json()) == 3
    newbie = make_member("n@example.in", "N", verified=False)
    assert {u["id"] for u in admin.get("/admin/users", params={"id_verified": "false"}).json()} == {newbie.id, admin.id}
    assert host.get("/admin/homes").status_code == 403
    assert host.get("/admin/users").status_code == 403


def test_swap_listing_includes_display_fields(client, world):
    world["guest"].post("/swaps", swap_body(world["home_id"]))
    s = world["guest"].get("/me/swaps").json()[0]
    assert (s["home_title"], s["city"], s["host_id"]) == ("Beach flat", "Goa", world["host"].id)


def test_web_client_is_served_statically(client):
    r = client.get("/app/")
    assert r.status_code == 200 and "HomeSwap" in r.text
    assert client.get("/app/app.js").status_code == 200
