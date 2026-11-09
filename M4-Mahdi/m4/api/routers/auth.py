"""Authentication endpoints: login, refresh (rotation), logout, current user."""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ...auth.deps import get_current_user
from ...auth.security import (
    AuthError,
    authenticate,
    create_access_token,
    issue_refresh_token,
    revoke_refresh_token,
    rotate_refresh_token,
)
from ...config import get_settings
from ...db.models import User
from ...db.session import get_db
from ..schemas import LoginRequest, RefreshRequest, TokenResponse, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])
COOKIE = "m4_refresh"


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(COOKIE, token, httponly=True, secure=s.refresh_cookie_secure, samesite="lax",
                    max_age=s.refresh_token_days * 86400, path="/api/auth")


def _tokens(db: Session, user: User, resp: Response) -> TokenResponse:
    access, exp = create_access_token(user)
    refresh = issue_refresh_token(db, user)
    _set_cookie(resp, refresh)
    return TokenResponse(access_token=access, expires_in=exp, refresh_token=refresh, user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse, responses={401: {"description": "Invalid credentials"}})
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    try:
        user = authenticate(db, body.login, body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return _tokens(db, user, response)


@router.post("/refresh", response_model=TokenResponse, responses={401: {"description": "Expired or revoked"}})
def refresh(response: Response, body: RefreshRequest | None = None, m4_refresh: str | None = Cookie(None),
            db: Session = Depends(get_db)):
    token = (body.refresh_token if body else None) or m4_refresh
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token.")
    try:
        user, new_refresh = rotate_refresh_token(db, token)
    except AuthError as exc:
        response.delete_cookie(COOKIE, path="/api/auth")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    _set_cookie(response, new_refresh)
    access, exp = create_access_token(user)
    return TokenResponse(access_token=access, expires_in=exp, refresh_token=new_refresh, user=UserOut.model_validate(user))


@router.post("/logout", status_code=204)
def logout(response: Response, body: RefreshRequest | None = None, m4_refresh: str | None = Cookie(None),
           db: Session = Depends(get_db)):
    token = (body.refresh_token if body else None) or m4_refresh
    if token:
        revoke_refresh_token(db, token)
    response.delete_cookie(COOKIE, path="/api/auth")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
