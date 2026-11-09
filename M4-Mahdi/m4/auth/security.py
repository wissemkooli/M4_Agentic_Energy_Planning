"""Password hashing (Argon2id) and token management.

- Access tokens: short-lived JWT (HS256) carrying user id and role.
- Refresh tokens: random opaque strings; only their SHA-256 hash is stored, so a
  database leak does not expose usable tokens. Refresh tokens are rotated on every use
  and revoked on logout.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import RefreshToken, User

_hasher = PasswordHasher()
MIN_PASSWORD_LENGTH = 10
# A token replaced by rotation stays usable for a few seconds: if the browser aborts the
# refresh response (e.g. two quick page reloads) it still holds the previous cookie.
ROTATION_GRACE_SECONDS = 30


class AuthError(Exception):
    pass


def validate_password_strength(password: str) -> None:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if not any(c.isdigit() for c in password):
        problems.append("a digit")
    if not any(c.isalpha() for c in password):
        problems.append("a letter")
    if problems:
        raise ValueError("Password must contain " + ", ".join(problems) + ".")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(user: User) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=s.access_token_minutes)
    payload = {"sub": str(user.id), "role": user.role, "type": "access", "iat": int(now.timestamp()),
               "exp": int(exp.timestamp()), "jti": secrets.token_hex(8)}
    return jwt.encode(payload, s.jwt_secret_key, algorithm=s.jwt_algorithm), s.access_token_minutes * 60


def decode_access_token(token: str) -> dict:
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Session expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid authentication token.") from exc
    if payload.get("type") != "access":
        raise AuthError("Invalid token type.")
    return payload


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_refresh_token(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(48)
    db.add(RefreshToken(user_id=user.id, token_hash=_hash_token(token),
                        expires_at=datetime.now(timezone.utc) + timedelta(days=get_settings().refresh_token_days)))
    db.commit()
    return token


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def rotate_refresh_token(db: Session, token: str) -> tuple[User, str]:
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(token)))
    now = datetime.now(timezone.utc)
    if row is None:
        raise AuthError("Invalid refresh token. Please sign in again.")
    if row.revoked_at is not None:
        in_grace = (row.revoked_reason == "rotated"
                    and (now - _aware(row.revoked_at)).total_seconds() <= ROTATION_GRACE_SECONDS)
        if not in_grace:
            raise AuthError("Invalid refresh token. Please sign in again.")
    if _aware(row.expires_at) < now:
        raise AuthError("Session expired. Please sign in again.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AuthError("Account disabled.")
    if row.revoked_at is None:
        row.revoked_at, row.revoked_reason = now, "rotated"
        db.commit()
    return user, issue_refresh_token(db, user)


def revoke_refresh_token(db: Session, token: str) -> None:
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash_token(token)))
    if row is not None and row.revoked_reason != "logout":
        row.revoked_at, row.revoked_reason = datetime.now(timezone.utc), "logout"
        db.commit()


def authenticate(db: Session, login: str, password: str) -> User:
    login = login.strip().lower()
    user = db.scalar(select(User).where((User.email == login) | (User.username == login)))
    if user is None:
        _hasher.hash(password)  # equalize timing with the existing-user path
        raise AuthError("Invalid username/email or password.")
    if not verify_password(password, user.password_hash):
        raise AuthError("Invalid username/email or password.")
    if not user.is_active:
        raise AuthError("Account disabled. Contact an administrator.")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return user


def create_user(db: Session, email: str, username: str, password: str, role: str = "USER",
                full_name: str = "") -> User:
    email, username = email.strip().lower(), username.strip().lower()
    if role not in {"USER", "ADMIN"}:
        raise ValueError("role must be USER or ADMIN")
    validate_password_strength(password)
    if db.scalar(select(User).where((User.email == email) | (User.username == username))):
        raise ValueError("A user with this email or username already exists.")
    user = User(email=email, username=username, full_name=full_name, password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
