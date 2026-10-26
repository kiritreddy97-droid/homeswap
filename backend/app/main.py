from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services
from .db import Base, engine, get_db
from .models import Home, Swap, User


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="HomeSwap India", lifespan=lifespan)


class UserIn(BaseModel):
    name: str
    email: str


class HomeIn(BaseModel):
    owner_id: int
    city: str
    title: str
    bedrooms: int = 1
    max_guests: int = 2
    points_per_night: int = 100


class SwapIn(BaseModel):
    guest_id: int
    home_id: int
    start: date
    end: date


def _get(db: Session, model, id_: int):
    obj = db.get(model, id_)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} {id_} not found")
    return obj


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/users", status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db)):
    user = User(**body.model_dump())
    db.add(user)
    db.commit()
    return {"id": user.id}


@app.post("/users/{user_id}/verify")
def verify_user(user_id: int, db: Session = Depends(get_db)):
    # In production this is called by the KYC provider webhook, never by the client.
    user = _get(db, User, user_id)
    user.id_verified = True
    user.membership_active = True
    db.commit()
    return {"id_verified": True}


@app.get("/users/{user_id}/balance")
def get_balance(user_id: int, db: Session = Depends(get_db)):
    _get(db, User, user_id)
    return {"points": services.balance(db, user_id)}


@app.post("/homes", status_code=201)
def create_home(body: HomeIn, db: Session = Depends(get_db)):
    _get(db, User, body.owner_id)
    home = Home(**body.model_dump())
    db.add(home)
    db.commit()
    return {"id": home.id}


@app.post("/homes/{home_id}/verify")
def verify_home(home_id: int, db: Session = Depends(get_db)):
    services.verify_home(db, _get(db, Home, home_id))
    db.commit()
    return {"property_verified": True}


@app.get("/homes")
def list_homes(city: str | None = None, db: Session = Depends(get_db)):
    q = select(Home).where(Home.property_verified.is_(True))
    if city:
        q = q.where(Home.city.ilike(city))
    return [
        {"id": h.id, "city": h.city, "title": h.title, "bedrooms": h.bedrooms, "points_per_night": h.points_per_night}
        for h in db.scalars(q)
    ]


@app.post("/swaps", status_code=201)
def request_swap(body: SwapIn, db: Session = Depends(get_db)):
    guest, home = _get(db, User, body.guest_id), _get(db, Home, body.home_id)
    try:
        swap = services.request_swap(db, guest, home, body.start, body.end)
    except services.SwapError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return {"id": swap.id, "points_cost": swap.points_cost, "status": swap.status}


@app.post("/swaps/{swap_id}/approve")
def approve(swap_id: int, db: Session = Depends(get_db)):
    try:
        swap = services.approve_swap(db, _get(db, Swap, swap_id))
    except services.SwapError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return {"status": swap.status}


@app.post("/swaps/{swap_id}/cancel")
def cancel(swap_id: int, db: Session = Depends(get_db)):
    try:
        swap = services.cancel_swap(db, _get(db, Swap, swap_id))
    except services.SwapError as e:
        raise HTTPException(409, str(e))
    db.commit()
    return {"status": swap.status}
