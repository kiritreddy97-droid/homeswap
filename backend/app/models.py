from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .timeutil import now


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="member")  # member|admin
    id_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    membership_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Home(Base):
    __tablename__ = "homes"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    city: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(200))
    bedrooms: Mapped[int] = mapped_column(Integer, default=1)
    max_guests: Mapped[int] = mapped_column(Integer, default=2)
    points_per_night: Mapped[int] = mapped_column(Integer, default=100)
    property_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    listing_bonus_paid: Mapped[bool] = mapped_column(Boolean, default=False)

    owner: Mapped[User] = relationship()


class Swap(Base):
    __tablename__ = "swaps"

    id: Mapped[int] = mapped_column(primary_key=True)
    guest_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"))
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    points_cost: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested|approved|cancelled|completed

    home: Mapped[Home] = relationship()


class PointsLedger(Base):
    """Append-only; a user's balance is the sum of their deltas."""

    __tablename__ = "points_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(60))
    swap_id: Mapped[int | None] = mapped_column(ForeignKey("swaps.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OtpCode(Base):
    """One-time login code. Only a keyed hash is stored, never the code itself."""

    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    identifier: Mapped[str] = mapped_column(String(200), index=True)  # normalised email or +phone
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Review(Base):
    """Left by a participant of a completed swap about the other participant."""

    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("swap_id", "reviewer_id", name="uq_review_swap_reviewer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    swap_id: Mapped[int] = mapped_column(ForeignKey("swaps.id"), index=True)
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reviewee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id"), index=True)
    rating: Mapped[int] = mapped_column(Integer)  # 1..5
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DamageClaim(Base):
    """Host's record of damage after a completed stay; an admin resolves it. No money or points move automatically."""

    __tablename__ = "damage_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    swap_id: Mapped[int] = mapped_column(ForeignKey("swaps.id"), unique=True)
    claimant_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    description: Mapped[str] = mapped_column(Text)
    amount_claimed: Mapped[int] = mapped_column(Integer)  # integer rupees
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|approved|rejected
    resolution_note: Mapped[str] = mapped_column(Text, default="")
    amount_awarded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
