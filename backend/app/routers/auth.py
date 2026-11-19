from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .. import auth, models
from ..database import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_MAX_AGE = 365 * 86400  # the browser keeps it; the server-side session decides when it ends


class LoginIn(BaseModel):
    password: str = Field(max_length=auth.MAX_PASSWORD)


class PasswordIn(BaseModel):
    current: Optional[str] = Field(default=None, max_length=auth.MAX_PASSWORD)
    new: str = Field(min_length=auth.MIN_PASSWORD, max_length=auth.MAX_PASSWORD)

    @field_validator("new")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Password cannot be only whitespace")
        return v


class RemovePasswordIn(BaseModel):
    current: str = Field(max_length=auth.MAX_PASSWORD)


class AuthSettingsIn(BaseModel):
    guest_access: Literal["none", "read"]


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def _principal(request: Request) -> auth.Principal:
    return request.state.principal


def _require_write(request: Request) -> auth.Principal:
    principal = _principal(request)
    if not principal.can_write:
        raise HTTPException(401, "Authentication required")
    return principal


def _require_manage(request: Request) -> auth.Principal:
    """API tokens and guest access only exist while authentication is on, and only a signed-in
    caller may touch them. Credentials made while auth is off would otherwise survive into the
    "on" state."""
    principal = _principal(request)
    if not principal.enabled:
        if principal.has_password:  # a password exists, but STARTPAGE_AUTH_DISABLE is switching it off
            raise HTTPException(409, "Authentication is switched off by STARTPAGE_AUTH_DISABLE; API tokens and guest access are unavailable")
        raise HTTPException(409, "Set a password first: API tokens and guest access need authentication to be on")
    if not principal.authenticated:
        raise HTTPException(401, "Authentication required")
    return principal


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        auth.COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax", path="/",
        secure=auth.is_https(request),
    )


def _check_current(request: Request, db: Session, current: Optional[str]) -> None:
    """The existing password is required to change or remove it (also throttled like login)."""
    ip = auth.client_ip(request)
    wait = auth.throttle_reserve(ip)  # counts this attempt before the slow hash runs
    if wait:
        raise HTTPException(429, "Too many attempts", headers={"Retry-After": str(wait)})
    if not auth.verify_password(current or "", auth.get_config(db)["password_hash"]):
        raise HTTPException(403, "Current password is incorrect")
    auth.throttle_release(ip)


@router.get("/status")
def status(request: Request):
    p = _principal(request)
    return {
        "enabled": p.enabled,
        "has_password": p.has_password,
        "authenticated": p.authenticated,
        "guest_access": p.guest_access,
        "can_write": p.can_write,
        "user": p.user,
        "method": p.method,
    }


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = auth.client_ip(request)
    wait = auth.throttle_reserve(ip)  # counts this attempt before the slow hash runs
    if wait:
        raise HTTPException(429, "Too many attempts", headers={"Retry-After": str(wait)})
    # One response for every failure cause (wrong password, no password set, auth disabled).
    ok = auth.verify_password(body.password, auth.get_config(db)["password_hash"]) and not auth.disabled()
    if not ok:
        raise HTTPException(401, "Invalid password")
    auth.throttle_release(ip)
    _set_cookie(request, response, auth.create_session(db))
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    cookie = request.cookies.get(auth.COOKIE)
    if cookie:
        auth.drop_session_by_cookie(db, cookie)
    response.delete_cookie(auth.COOKIE, path="/", httponly=True, samesite="lax")
    return {"ok": True}


@router.post("/password")
def set_password(body: PasswordIn, request: Request, response: Response, db: Session = Depends(get_db)):
    principal = _principal(request)
    has_password = bool(auth.get_config(db)["password_hash"])
    if principal.enabled and not principal.authenticated:
        raise HTTPException(401, "Authentication required")
    if has_password:
        _check_current(request, db, body.current)
    # With auth off this sets the first password (the status quo is that anyone may).
    if not has_password and not principal.enabled:
        # Whatever exists from the time auth was off was not made by the owner: wipe it.
        auth.revoke_tokens(db)
        auth.save_config(db, password_hash=auth.hash_password(body.new), guest_access="none")
    else:
        # A change keeps API tokens: they are independent credentials (revoke them separately).
        auth.save_config(db, password_hash=auth.hash_password(body.new))
    auth.revoke_sessions(db)  # every other browser has to sign in again
    _set_cookie(request, response, auth.create_session(db))
    return {"ok": True}


@router.delete("/password")
def remove_password(body: RemovePasswordIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _require_write(request)
    if not auth.get_config(db)["password_hash"]:
        raise HTTPException(400, "No password is set")
    _check_current(request, db, body.current)
    # Auth goes off, so nothing issued while it was on may stay valid for the next time it is on.
    auth.save_config(db, password_hash="", guest_access="none")
    auth.revoke_sessions(db)
    auth.revoke_tokens(db)
    response.delete_cookie(auth.COOKIE, path="/", httponly=True, samesite="lax")
    return {"ok": True}


@router.patch("/settings")
def update_auth_settings(body: AuthSettingsIn, request: Request, db: Session = Depends(get_db)):
    _require_manage(request)
    cfg = auth.save_config(db, guest_access=body.guest_access)
    return {"guest_access": cfg["guest_access"]}


def _token_out(t: models.ApiToken) -> dict:
    return {"id": t.id, "name": t.name, "created_at": t.created_at, "last_used": t.last_used}


@router.get("/tokens")
def list_tokens(request: Request, db: Session = Depends(get_db)):
    _require_manage(request)
    rows = db.query(models.ApiToken).order_by(models.ApiToken.created_at).all()
    return [_token_out(t) for t in rows]


@router.post("/tokens", status_code=201)
def create_token(body: TokenIn, request: Request, db: Session = Depends(get_db)):
    _require_manage(request)
    row, token = auth.create_token(db, body.name.strip() or "token")
    return {**_token_out(row), "token": token}  # the only time the plaintext is shown


@router.delete("/tokens")
def revoke_all_tokens(request: Request, db: Session = Depends(get_db)):
    _require_manage(request)
    return {"revoked": auth.revoke_tokens(db)}


@router.delete("/tokens/{token_id}", status_code=204)
def delete_token(token_id: str, request: Request, db: Session = Depends(get_db)):
    _require_manage(request)
    row = db.get(models.ApiToken, token_id)
    if not row:
        raise HTTPException(404, "Token not found")
    db.delete(row)
    db.commit()
