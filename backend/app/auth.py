import hmac
from datetime import timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import otp
from .config import Settings, get_settings
from .db import get_db
from .models import OtpCode, User
from .timeutil import as_utc, now

MAX_OTP_ATTEMPTS = 5
MAX_OTP_REQUESTS_PER_WINDOW = 3
OTP_REQUEST_WINDOW = timedelta(minutes=10)

_bearer = HTTPBearer(auto_error=False)


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def issue_otp(db: Session, settings: Settings, sender: otp.OtpSender, identifier: str) -> None:
    recent = db.scalar(
        select(func.count(OtpCode.id)).where(
            OtpCode.identifier == identifier, OtpCode.created_at > now() - OTP_REQUEST_WINDOW
        )
    )
    if recent >= MAX_OTP_REQUESTS_PER_WINDOW:
        raise AuthError("too many codes requested, try again later", 429)
    code = otp.generate_code()
    db.add(
        OtpCode(
            identifier=identifier,
            code_hash=otp.hash_code(settings.jwt_secret, identifier, code),
            expires_at=now() + timedelta(minutes=settings.otp_ttl_minutes),
        )
    )
    db.flush()
    sender.send(identifier, code)


def check_otp(db: Session, settings: Settings, identifier: str, code: str) -> bool:
    """True if the code is the latest live one for this identifier. Failed attempts are counted (caller commits)."""
    row = db.scalar(
        select(OtpCode)
        .where(OtpCode.identifier == identifier, OtpCode.consumed.is_(False))
        .order_by(OtpCode.id.desc())
        .limit(1)
    )
    if row is None or as_utc(row.expires_at) < now() or row.attempts >= MAX_OTP_ATTEMPTS:
        return False
    row.attempts += 1
    # constant-time comparison; wrong guesses burn attempts, a right one burns the code
    if not hmac.compare_digest(row.code_hash, otp.hash_code(settings.jwt_secret, identifier, code)):
        return False
    row.consumed = True
    return True


def get_or_create_user(db: Session, settings: Settings, identifier: str, name: str) -> User:
    column = User.email if "@" in identifier else User.phone
    user = db.scalar(select(User).where(column == identifier))
    if user is None:
        user = User(name=name, **{"email" if "@" in identifier else "phone": identifier})
        db.add(user)
    elif name and not user.name:
        user.name = name
    if identifier in settings.admin_contacts:
        user.role = "admin"
    db.flush()
    return user


def create_token(settings: Settings, user: User) -> str:
    issued = now()
    claims = {
        "sub": str(user.id),
        "role": user.role,
        "iat": issued,
        "exp": issued + timedelta(minutes=settings.jwt_ttl_minutes),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    unauthorised = HTTPException(401, "not authenticated", headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorised
    try:
        claims = jwt.decode(
            creds.credentials, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]}
        )
        user = db.get(User, int(claims["sub"]))
    except (jwt.PyJWTError, ValueError):
        raise unauthorised from None
    if user is None:
        raise unauthorised
    return user  # role comes from the database, so demoting an admin takes effect immediately


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "admin only")
    return user
