import hashlib
import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import auth, otp, services
from .config import Settings, get_settings
from .db import get_db
from .middleware import RateLimiter, RequestMiddleware, configure_logging
from .models import DamageClaim, Home, Review, Swap, User

log = logging.getLogger("homeswap")

_startup = get_settings()
limiter = RateLimiter(_startup.rate_limit_per_minute)
auth_limiter = RateLimiter(_startup.auth_rate_limit_per_minute)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.validate()
    configure_logging(settings.log_level)
    # Schema is managed by Alembic (`alembic upgrade head`), never created here.
    yield


app = FastAPI(title="HomeSwap India", lifespan=lifespan)
app.add_middleware(RequestMiddleware, limiter=limiter, auth_limiter=auth_limiter)


def get_otp_sender(settings: Settings = Depends(get_settings)) -> otp.OtpSender:
    return otp.get_sender(settings.otp_sender)


# ---------- schemas ----------


class OtpRequestIn(BaseModel):
    identifier: str = Field(description="Email address or phone number")


class OtpVerifyIn(BaseModel):
    identifier: str
    code: str = Field(min_length=6, max_length=6)
    name: str = Field(default="", max_length=120)


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class HomeIn(BaseModel):
    city: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    bedrooms: int = Field(default=1, ge=1, le=20)
    max_guests: int = Field(default=2, ge=1, le=50)
    points_per_night: int = Field(default=100, ge=1, le=100_000)


class SwapIn(BaseModel):
    home_id: int
    start: date
    end: date


class ReviewIn(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class ClaimIn(BaseModel):
    description: str = Field(min_length=1, max_length=4000)
    amount: int = Field(ge=0, le=10_000_000, description="Claimed damage in integer rupees")


class ResolveIn(BaseModel):
    approve: bool
    amount_awarded: int | None = Field(default=None, ge=0)
    note: str = Field(default="", max_length=4000)


class KycEvent(BaseModel):
    user_id: int
    status: str = Field(pattern="^(verified|rejected)$")


# ---------- helpers ----------


def _get(db: Session, model, id_: int):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {id_} not found")
    return obj


def _conflict(fn, *args):
    try:
        return fn(*args)
    except services.SwapError as e:
        raise HTTPException(409, str(e)) from None


def _is_participant(user: User, swap: Swap) -> bool:
    return user.id in (swap.guest_id, swap.home.owner_id)


def _swap_for(db: Session, swap_id: int, user: User, *, host_only: bool = False) -> Swap:
    """Loads a swap the caller may act on; 404 (not 403) for strangers so ids are not enumerable."""
    swap = _get(db, Swap, swap_id)
    allowed = user.role == "admin" or (
        user.id == swap.home.owner_id if host_only else _is_participant(user, swap)
    )
    if not allowed:
        raise HTTPException(404, f"Swap {swap_id} not found")
    return swap


def _swap_out(s: Swap) -> dict:
    return {
        "id": s.id, "guest_id": s.guest_id, "home_id": s.home_id, "home_title": s.home.title, "city": s.home.city,
        "host_id": s.home.owner_id, "start": s.start, "end": s.end,
        "points_cost": s.points_cost, "status": s.status,
    }  # fmt: skip


def _review_out(r: Review) -> dict:
    return {
        "id": r.id, "swap_id": r.swap_id, "reviewer_id": r.reviewer_id, "reviewee_id": r.reviewee_id,
        "home_id": r.home_id, "rating": r.rating, "comment": r.comment, "created_at": r.created_at,
    }  # fmt: skip


def _claim_out(c: DamageClaim) -> dict:
    return {
        "id": c.id, "swap_id": c.swap_id, "claimant_id": c.claimant_id, "description": c.description,
        "amount_claimed": c.amount_claimed, "status": c.status, "amount_awarded": c.amount_awarded,
        "resolution_note": c.resolution_note, "created_at": c.created_at, "resolved_at": c.resolved_at,
    }  # fmt: skip


def _home_out(h: Home) -> dict:
    return {
        "id": h.id, "city": h.city, "title": h.title, "bedrooms": h.bedrooms, "max_guests": h.max_guests,
        "points_per_night": h.points_per_night, "property_verified": h.property_verified,
    }  # fmt: skip


def _user_out(u: User) -> dict:
    return {
        "id": u.id, "name": u.name, "email": u.email, "phone": u.phone, "role": u.role,
        "id_verified": u.id_verified, "membership_active": u.membership_active,
    }  # fmt: skip


# ---------- public ----------


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/otp/request", status_code=202)
def request_otp(
    body: OtpRequestIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    sender: otp.OtpSender = Depends(get_otp_sender),
):
    try:
        identifier = otp.normalise_identifier(body.identifier)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    try:
        auth.issue_otp(db, settings, sender, identifier)
    except auth.AuthError as e:
        raise HTTPException(e.status, str(e)) from None
    db.commit()
    return {"detail": "code sent"}  # same answer whether or not the account exists


@app.post("/auth/otp/verify")
def verify_otp(body: OtpVerifyIn, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    try:
        identifier = otp.normalise_identifier(body.identifier)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    ok = auth.check_otp(db, settings, identifier, body.code)
    if not ok:
        db.commit()  # persist the failed-attempt counter
        raise HTTPException(401, "invalid or expired code")
    user = auth.get_or_create_user(db, settings, identifier, body.name.strip())
    db.commit()
    return {"access_token": auth.create_token(settings, user), "token_type": "bearer", "user": _user_out(user)}


# ---------- KYC: admin or provider webhook only ----------


@app.post("/kyc/webhook")
async def kyc_webhook(request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """KYC provider callback. Body must be signed: header `X-Signature: sha256=<hex HMAC of raw body>`.
    Adapt the payload mapping to the chosen provider (see docs/03-architecture.md)."""
    if not settings.kyc_webhook_secret:
        raise HTTPException(503, "KYC webhook is not configured")
    raw = await request.body()
    expected = "sha256=" + hmac.new(settings.kyc_webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, request.headers.get("x-signature", "")):
        raise HTTPException(401, "bad signature")
    try:
        event = KycEvent.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(422, "invalid payload") from None
    user = _get(db, User, event.user_id)
    user.id_verified = event.status == "verified"
    # Membership is activated with verification until the paid-membership flow exists (needs a payment gateway).
    user.membership_active = user.id_verified
    db.commit()
    return {"id_verified": user.id_verified}


@app.post("/admin/users/{user_id}/verify")
def admin_verify_user(user_id: int, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    user = _get(db, User, user_id)
    user.id_verified = True
    user.membership_active = True
    db.commit()
    return {"id_verified": True}


@app.get("/admin/users")
def admin_users(id_verified: bool | None = None, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    q = select(User).order_by(User.id)
    if id_verified is not None:
        q = q.where(User.id_verified.is_(id_verified))
    return [_user_out(u) for u in db.scalars(q)]


@app.get("/admin/homes")
def admin_homes(verified: bool | None = None, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    q = select(Home).order_by(Home.id)
    if verified is not None:
        q = q.where(Home.property_verified.is_(verified))
    return [{**_home_out(h), "owner_id": h.owner_id} for h in db.scalars(q)]


@app.post("/admin/homes/{home_id}/verify")
def admin_verify_home(home_id: int, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    services.verify_home(db, _get(db, Home, home_id))
    db.commit()
    return {"property_verified": True}


# ---------- members ----------


@app.get("/me")
def me(user: User = Depends(auth.current_user)):
    return _user_out(user)


@app.patch("/me")
def update_me(body: ProfileIn, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    user.name = body.name.strip()
    db.commit()
    return _user_out(user)


@app.get("/me/balance")
def my_balance(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    return {"points": services.balance(db, user.id)}


@app.get("/me/swaps")
def my_swaps(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    q = select(Swap).join(Home).where(or_(Swap.guest_id == user.id, Home.owner_id == user.id)).order_by(Swap.id)
    return [_swap_out(s) for s in db.scalars(q)]


@app.get("/me/homes")
def my_homes(user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    return [_home_out(h) for h in db.scalars(select(Home).where(Home.owner_id == user.id).order_by(Home.id))]


@app.get("/users/{user_id}/balance")
def get_balance(user_id: int, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    if user_id != user.id and user.role != "admin":
        raise HTTPException(404, f"User {user_id} not found")
    _get(db, User, user_id)
    return {"points": services.balance(db, user_id)}


@app.get("/users/{user_id}/reviews")
def user_reviews(user_id: int, _: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    _get(db, User, user_id)
    rows = list(db.scalars(select(Review).where(Review.reviewee_id == user_id).order_by(Review.id)))
    avg = sum(r.rating for r in rows) / len(rows) if rows else None
    return {"average_rating": avg, "count": len(rows), "reviews": [_review_out(r) for r in rows]}


@app.post("/homes", status_code=201)
def create_home(body: HomeIn, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    home = Home(owner_id=user.id, **body.model_dump())
    db.add(home)
    db.commit()
    return {"id": home.id}


@app.get("/homes")
def list_homes(city: str | None = None, _: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    q = select(Home).where(Home.property_verified.is_(True))
    if city:
        q = q.where(Home.city.ilike(city))
    return [_home_out(h) for h in db.scalars(q)]


@app.get("/homes/{home_id}/reviews")
def home_reviews(home_id: int, _: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    home = _get(db, Home, home_id)
    # Only guests' reviews describe the home; hosts' reviews of guests belong to the guest's profile.
    q = select(Review).where(Review.home_id == home.id, Review.reviewee_id == home.owner_id).order_by(Review.id)
    rows = list(db.scalars(q))
    avg = sum(r.rating for r in rows) / len(rows) if rows else None
    return {"average_rating": avg, "count": len(rows), "reviews": [_review_out(r) for r in rows]}


# ---------- swaps ----------


@app.post("/swaps", status_code=201)
def request_swap(body: SwapIn, guest: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    home = _get(db, Home, body.home_id)
    swap = _conflict(services.request_swap, db, guest, home, body.start, body.end)
    db.commit()
    return {"id": swap.id, "points_cost": swap.points_cost, "status": swap.status}


@app.get("/swaps/{swap_id}")
def get_swap(swap_id: int, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    return _swap_out(_swap_for(db, swap_id, user))


@app.post("/swaps/{swap_id}/approve")
def approve(swap_id: int, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    swap = _conflict(services.approve_swap, db, _swap_for(db, swap_id, user, host_only=True))
    db.commit()
    return {"status": swap.status}


@app.post("/swaps/{swap_id}/cancel")
def cancel(swap_id: int, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    swap = _conflict(services.cancel_swap, db, _swap_for(db, swap_id, user))
    db.commit()
    return {"status": swap.status}


@app.post("/swaps/{swap_id}/complete")
def complete(swap_id: int, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    swap = _conflict(services.complete_swap, db, _swap_for(db, swap_id, user))
    db.commit()
    return {"status": swap.status}


@app.post("/swaps/{swap_id}/reviews", status_code=201)
def create_review(swap_id: int, body: ReviewIn, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    swap = _get(db, Swap, swap_id)
    if not _is_participant(user, swap):  # admins are not participants and cannot review
        raise HTTPException(404, f"Swap {swap_id} not found")
    review = _conflict(services.add_review, db, swap, user, body.rating, body.comment)
    db.commit()
    return _review_out(review)


@app.post("/swaps/{swap_id}/damage-claim", status_code=201)
def create_claim(swap_id: int, body: ClaimIn, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    swap = _get(db, Swap, swap_id)
    if not _is_participant(user, swap):
        raise HTTPException(404, f"Swap {swap_id} not found")
    claim = _conflict(services.file_damage_claim, db, swap, user, body.description, body.amount)
    db.commit()
    return _claim_out(claim)


# ---------- damage claims ----------


@app.get("/damage-claims/{claim_id}")
def get_claim(claim_id: int, user: User = Depends(auth.current_user), db: Session = Depends(get_db)):
    claim = _get(db, DamageClaim, claim_id)
    swap = db.get(Swap, claim.swap_id)
    if user.role != "admin" and not _is_participant(user, swap):
        raise HTTPException(404, f"DamageClaim {claim_id} not found")
    return _claim_out(claim)


@app.get("/admin/damage-claims")
def list_claims(status: str | None = None, _: User = Depends(auth.require_admin), db: Session = Depends(get_db)):
    q = select(DamageClaim).order_by(DamageClaim.id)
    if status:
        q = q.where(DamageClaim.status == status)
    return [_claim_out(c) for c in db.scalars(q)]


@app.post("/admin/damage-claims/{claim_id}/resolve")
def resolve_claim(
    claim_id: int, body: ResolveIn, admin: User = Depends(auth.require_admin), db: Session = Depends(get_db)
):
    claim = _conflict(
        services.resolve_damage_claim, db, _get(db, DamageClaim, claim_id), admin,
        body.approve, body.amount_awarded, body.note,
    )  # fmt: skip
    db.commit()
    return _claim_out(claim)


# The web client is plain static files served from the same origin (no CORS, no build step).
_WEB_DIR = Path(os.getenv("WEB_DIR", Path(__file__).resolve().parents[2] / "web" / "app"))
if _WEB_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=_WEB_DIR, html=True), name="web")
