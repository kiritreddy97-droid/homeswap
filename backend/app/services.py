from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import DamageClaim, Home, PointsLedger, Review, Swap, User
from .timeutil import now, today

LISTING_BONUS = 200  # one-time points for a verified home, so new members can travel first
CLAIM_WINDOW_DAYS = 14  # hosts can file a damage claim this long after checkout


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
    if start < today():
        raise SwapError("stay cannot start in the past")
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


def complete_swap(db: Session, swap: Swap) -> Swap:
    """Marks an approved stay as completed once checkout has passed; unlocks reviews and damage claims."""
    if swap.status != "approved":
        raise SwapError(f"swap is {swap.status}")
    if swap.end > today():
        raise SwapError("stay has not ended yet")
    swap.status = "completed"
    db.flush()
    return swap


def add_review(db: Session, swap: Swap, reviewer: User, rating: int, comment: str) -> Review:
    if swap.status != "completed":
        raise SwapError("reviews are only allowed after a completed stay")
    host_id = swap.home.owner_id
    if reviewer.id == swap.guest_id:
        reviewee_id = host_id
    elif reviewer.id == host_id:
        reviewee_id = swap.guest_id
    else:
        raise SwapError("only participants of the stay can review it")
    if not 1 <= rating <= 5:
        raise SwapError("rating must be between 1 and 5")
    if db.scalar(select(Review.id).where(Review.swap_id == swap.id, Review.reviewer_id == reviewer.id)):
        raise SwapError("you already reviewed this stay")
    review = Review(
        swap_id=swap.id, reviewer_id=reviewer.id, reviewee_id=reviewee_id, home_id=swap.home_id,
        rating=rating, comment=comment,
    )  # fmt: skip
    db.add(review)
    db.flush()
    return review


def file_damage_claim(db: Session, swap: Swap, claimant: User, description: str, amount: int) -> DamageClaim:
    if swap.status != "completed":
        raise SwapError("damage claims are only allowed after a completed stay")
    if claimant.id != swap.home.owner_id:
        raise SwapError("only the host can claim damage")
    if (today() - swap.end).days > CLAIM_WINDOW_DAYS:
        raise SwapError(f"claims must be filed within {CLAIM_WINDOW_DAYS} days of checkout")
    if amount < 0:
        raise SwapError("amount must not be negative")
    if db.scalar(select(DamageClaim.id).where(DamageClaim.swap_id == swap.id)):
        raise SwapError("a claim already exists for this stay")
    claim = DamageClaim(swap_id=swap.id, claimant_id=claimant.id, description=description, amount_claimed=amount)
    db.add(claim)
    db.flush()
    return claim


def resolve_damage_claim(
    db: Session, claim: DamageClaim, admin: User, approve: bool, amount_awarded: int | None, note: str
) -> DamageClaim:
    if claim.status != "open":
        raise SwapError(f"claim is {claim.status}")
    if approve:
        awarded = claim.amount_claimed if amount_awarded is None else amount_awarded
        if not 0 <= awarded <= claim.amount_claimed:
            raise SwapError("awarded amount must be between 0 and the claimed amount")
        claim.amount_awarded = awarded
    else:
        claim.amount_awarded = 0
    claim.status = "approved" if approve else "rejected"
    claim.resolution_note = note
    claim.resolved_by = admin.id
    claim.resolved_at = now()
    db.flush()
    return claim
