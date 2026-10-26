from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    id_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    membership_active: Mapped[bool] = mapped_column(Boolean, default=False)


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
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested|approved|rejected|cancelled

    home: Mapped[Home] = relationship()


class PointsLedger(Base):
    """Append-only; a user's balance is the sum of their deltas."""

    __tablename__ = "points_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(60))
    swap_id: Mapped[int | None] = mapped_column(ForeignKey("swaps.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
