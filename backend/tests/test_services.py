from datetime import timedelta

import pytest

from app import services
from app.models import Home, User
from app.timeutil import today

D0 = today() + timedelta(days=30)  # stays must start in the future


def d(offset: int):
    return D0 + timedelta(days=offset)


def _world(db):
    host = User(name="Asha", email="a@x.in", id_verified=True, membership_active=True)
    guest = User(name="Ravi", email="r@x.in", id_verified=True, membership_active=True)
    db.add_all([host, guest])
    db.flush()
    goa = Home(owner_id=host.id, city="Goa", title="Beach flat", points_per_night=100)
    mum = Home(owner_id=guest.id, city="Mumbai", title="Bandra 2BHK", points_per_night=100)
    db.add_all([goa, mum])
    db.flush()
    services.verify_home(db, goa)
    services.verify_home(db, mum)
    return host, guest, goa, mum


def test_listing_bonus_paid_once(db):
    host, _, goa, _ = _world(db)
    services.verify_home(db, goa)
    assert services.balance(db, host.id) == services.LISTING_BONUS


def test_points_move_on_approval(db):
    host, guest, goa, _ = _world(db)
    swap = services.request_swap(db, guest, goa, d(0), d(2))
    assert swap.points_cost == 200
    services.approve_swap(db, swap)
    assert services.balance(db, guest.id) == 0
    assert services.balance(db, host.id) == 400


def test_insufficient_points(db):
    _, guest, goa, _ = _world(db)
    with pytest.raises(services.SwapError, match="insufficient"):
        services.request_swap(db, guest, goa, d(0), d(3))


def test_overlap_blocked(db):
    host, guest, goa, _ = _world(db)
    services.approve_swap(db, services.request_swap(db, guest, goa, d(0), d(1)))
    third = User(name="T", email="t@x.in", id_verified=True, membership_active=True)
    db.add(third)
    db.flush()
    db.add(services.PointsLedger(user_id=third.id, delta=500, reason="test"))
    db.flush()
    with pytest.raises(services.SwapError, match="already booked"):
        services.request_swap(db, third, goa, d(0), d(1))


def test_unverified_guest_rejected(db):
    _, guest, goa, _ = _world(db)
    guest.id_verified = False
    with pytest.raises(services.SwapError, match="identity"):
        services.request_swap(db, guest, goa, d(0), d(1))


def test_cancel_refunds(db):
    host, guest, goa, _ = _world(db)
    swap = services.approve_swap(db, services.request_swap(db, guest, goa, d(0), d(1)))
    services.cancel_swap(db, swap)
    assert services.balance(db, guest.id) == 200
    assert services.balance(db, host.id) == 200


def test_past_stay_rejected(db):
    _, guest, goa, _ = _world(db)
    with pytest.raises(services.SwapError, match="past"):
        services.request_swap(db, guest, goa, today() - timedelta(days=2), today() - timedelta(days=1))
