from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Home, PointsLedger, Swap, User

LISTING_BONUS = 200  # one-time points for a verified home, so new members can travel first


class SwapError(Exception):
    pass


def balance(db: Session, user_id: int) -> int:
    return db.scalar(select(func.coalesce(func.sum(PointsLedger.delta), 0)).where(PointsLedger.user_id == user_id))


def nights(start: date, end: date) -> int:
    n = (end - start).days
    if n < 1:
        raise SwapError("stay must be at least one night")
    return n


def verify_home(db: Session, home: Home) -> None:
    """Called after document + photo checks pass. Pays the one-time listing bonus."""
    home.property_verified = True
    if not home.listing_bonus_paid:
        home.listing_bonus_paid = True
        db.add(PointsLedger(user_id=home.owner_id, delta=LISTING_BONUS, reason="listing_bonus"))
    db.flush()


def _overlaps(db: Session, home_id: int, start: date, end: date) -> bool:
    q = select(Swap.id).where(
        Swap.home_id == home_id, Swap.status == "approved", Swap.start < end, Swap.end > start
    )
    return db.scalar(q) is not None


def request_swap(db: Session, guest: User, home: Home, start: date, end: date) -> Swap:
    if not guest.id_verified:
        raise SwapError("guest identity not verified")
    if not guest.membership_active:
        raise SwapError("active membership required")
    if not home.property_verified:
        raise SwapError("home is not verified")
    if home.owner_id == guest.id:
        raise SwapError("cannot stay in your own home")
    cost = nights(start, end) * home.points_per_night
    if balance(db, guest.id) < cost:
        raise SwapError("insufficient points")
    if _overlaps(db, home.id, start, end):
        raise SwapError("home already booked for those dates")
    swap = Swap(guest_id=guest.id, home_id=home.id, start=start, end=end, points_cost=cost)
    db.add(swap)
    db.flush()
    return swap


def approve_swap(db: Session, swap: Swap) -> Swap:
    """Host approves: guest pays points, host earns them. Re-checks everything at approval time."""
    if swap.status != "requested":
        raise SwapError(f"swap is {swap.status}")
    if _overlaps(db, swap.home_id, swap.start, swap.end):
        raise SwapError("home already booked for those dates")
    if balance(db, swap.guest_id) < swap.points_cost:
        raise SwapError("guest no longer has enough points")
    swap.status = "approved"
    db.add(PointsLedger(user_id=swap.guest_id, delta=-swap.points_cost, reason="stay", swap_id=swap.id))
    db.add(PointsLedger(user_id=swap.home.owner_id, delta=swap.points_cost, reason="hosted", swap_id=swap.id))
    db.flush()
    return swap


def cancel_swap(db: Session, swap: Swap) -> Swap:
    """Cancelling an approved swap reverses the points movement."""
    if swap.status not in ("requested", "approved"):
        raise SwapError(f"swap is {swap.status}")
    if swap.status == "approved":
        db.add(PointsLedger(user_id=swap.guest_id, delta=swap.points_cost, reason="refund", swap_id=swap.id))
        db.add(PointsLedger(user_id=swap.home.owner_id, delta=-swap.points_cost, reason="refund", swap_id=swap.id))
    swap.status = "cancelled"
    db.flush()
    return swap
