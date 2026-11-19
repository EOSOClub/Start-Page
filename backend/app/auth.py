"""Optional authentication: one shared password, browser sessions, API tokens, proxy header.

Off by default. It is "enabled" once a password exists (or proxy-header auth is configured
through the environment), and enforced for `/api/*` by the middleware in main.py.
The config lives in `app_settings` under its own key, so it never appears in the
settings API, exports or backups.

`python -m app.auth reset` (run from `backend/`) removes the password, sessions and tokens.
"""
import base64
import hashlib
import hmac
import ipaddress
import logging
import math
import os
import secrets
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request
from sqlalchemy.orm import Session

from . import models

log = logging.getLogger("startpage.auth")

KEY = "auth"
COOKIE = "sp_session"
SESSION_TTL = timedelta(days=30)  # sliding: every use pushes the expiry out again
SESSION_MAX_AGE = timedelta(days=90)  # absolute: a session never outlives this, however often it is used
TOUCH_EVERY = timedelta(minutes=30)  # don't write on every request
MIN_PASSWORD = 8
MAX_PASSWORD = 1024
GUEST_MODES = ("none", "read")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
SCRYPT_MAXMEM = 64 * 1024 * 1024
# Guests (guest_access == "read") never get these, even with GET.
SENSITIVE_PREFIXES = ("/api/config/export", "/api/backups", "/api/auth", "/api/docs", "/api/openapi.json", "/api/discovery", "/api/templates")


def is_page_export(path: str) -> bool:
    """`/api/dashboards/<id>/export` (a page as portable JSON): writers only, like the whole-config export."""
    low = path.lower().rstrip("/")
    return low.startswith("/api/dashboards/") and low.endswith("/export")
# Reachable by anyone: the login screen needs them. Logout is idempotent and harmless.
OPEN_ROUTES = {("GET", "/api/auth/status"), ("POST", "/api/auth/login"), ("POST", "/api/auth/logout")}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC, like the health tables


# ---------- password hashing ----------
def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, maxmem=SCRYPT_MAXMEM, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${_b64(salt)}${_b64(dk)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. With no stored hash it still burns the same work, so timing
    does not reveal whether a password is set."""
    try:
        algo, n, r, p, salt, want = stored.split("$")
        if algo != "scrypt":
            raise ValueError(algo)
        salt_b, want_b = base64.b64decode(salt), base64.b64decode(want)
        params = {"n": int(n), "r": int(r), "p": int(p)}
    except ValueError:
        salt_b, want_b, params = b"\0" * 16, b"\0" * 32, dict(SCRYPT)
        password += "\0"  # can never match the dummy
    dk = hashlib.scrypt(password.encode(), salt=salt_b, maxmem=SCRYPT_MAXMEM, dklen=len(want_b), **params)
    return hmac.compare_digest(dk, want_b) and stored != ""


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- stored config ----------
def get_config(db: Session) -> dict:
    row = db.get(models.AppSetting, KEY)
    stored = (row.value if row else None) or {}
    guest = stored.get("guest_access")
    return {
        "password_hash": stored.get("password_hash") or "",
        "guest_access": guest if guest in GUEST_MODES else "none",
    }


def save_config(db: Session, **changes) -> dict:
    cfg = {**get_config(db), **changes}
    row = db.get(models.AppSetting, KEY)
    if row:
        row.value = cfg
    else:
        db.add(models.AppSetting(key=KEY, value=cfg))
    db.commit()
    return cfg


# ---------- environment: trusted proxies, header auth, kill switch ----------
def disabled() -> bool:
    return os.environ.get("STARTPAGE_AUTH_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


_nets_cache: tuple = (None, [])  # (env string it was parsed from, networks)


def _networks() -> list:
    """Parsed STARTPAGE_TRUSTED_PROXIES. Cached on the env string, so a changed value (tests) is
    re-read but a bad entry is only logged once."""
    global _nets_cache
    raw = os.environ.get("STARTPAGE_TRUSTED_PROXIES", "")
    if _nets_cache[0] == raw:
        return _nets_cache[1]
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            log.warning("STARTPAGE_TRUSTED_PROXIES: ignoring invalid entry %r", part)
    _nets_cache = (raw, nets)
    return nets


def _auth_header() -> str:
    return os.environ.get("STARTPAGE_AUTH_HEADER", "").strip().lower()


def proxy_auth_configured() -> bool:
    return bool(_auth_header()) and bool(_networks())


def is_trusted(ip: Optional[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return False
    addr = getattr(addr, "ipv4_mapped", None) or addr
    return any(addr.version == net.version and addr in net for net in _networks())


def api_path(request: Request) -> Optional[str]:
    """The request's /api path, or None when it is not under /api.

    Taken from the ASGI scope (never `request.url`, which is rebuilt from the Host header) and
    normalised, so `//api/x` and `/API/x` count as /api too: the guards fail closed on odd spellings.
    """
    path = "/" + str(request.scope.get("path", "")).lstrip("/")
    low = path.lower()
    if low == "/api" or low.startswith("/api/"):
        return "/api" + path[4:]
    return None


def peer_ip(request: Request) -> str:
    """The direct TCP peer. Uvicorn must run with --no-proxy-headers so this is never rewritten
    from X-Forwarded-For before it gets here; client_ip() below is the only place that header is read."""
    return request.client.host if request.client else ""


def client_ip(request: Request) -> str:
    """The caller's address: the direct peer, or behind a trusted proxy the nearest
    X-Forwarded-For entry that is not itself a trusted proxy."""
    peer = peer_ip(request)
    if is_trusted(peer):
        for hop in reversed([h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]):
            if not is_trusted(hop):
                return hop
    return peer


def is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    if is_trusted(peer_ip(request)):
        return request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"
    return False


# ---------- who is calling ----------
@dataclass
class Principal:
    enabled: bool = False
    authenticated: bool = False
    method: Optional[str] = None  # "session" | "token" | "proxy"
    user: Optional[str] = None
    guest_access: str = "none"
    session_id: Optional[str] = None
    has_password: bool = False

    @property
    def can_write(self) -> bool:
        return not self.enabled or self.authenticated

    @property
    def guest(self) -> bool:
        """Enabled, not signed in, but let in read-only."""
        return self.enabled and not self.authenticated


def is_guest(request: Request) -> bool:
    principal = getattr(request.state, "principal", None)
    return bool(principal and principal.guest)


def resolve(db: Session, request: Request) -> Principal:
    cfg = get_config(db)
    enabled = (bool(cfg["password_hash"]) or proxy_auth_configured()) and not disabled()
    principal = Principal(enabled=enabled, guest_access=cfg["guest_access"], has_password=bool(cfg["password_hash"]))
    if not enabled:
        return principal

    header = _auth_header()
    if proxy_auth_configured() and is_trusted(peer_ip(request)):
        user = request.headers.get(header, "").strip()
        if user:
            principal.authenticated, principal.method, principal.user = True, "proxy", user
            return principal

    now = _now()
    scheme, _, bearer = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer" and bearer.strip():
        tok = db.query(models.ApiToken).filter_by(token_hash=_sha(bearer.strip())).first()
        if tok:
            if tok.last_used is None or now - tok.last_used > TOUCH_EVERY:
                tok.last_used = now
                db.commit()
            principal.authenticated, principal.method, principal.user = True, "token", tok.name
            return principal

    cookie = request.cookies.get(COOKIE)
    if cookie:
        sess = db.query(models.AuthSession).filter_by(token_hash=_sha(cookie)).first()
        if sess and sess.expires_at > now and now - sess.created_at < SESSION_MAX_AGE:
            if now - sess.last_seen > TOUCH_EVERY:
                sess.last_seen = now
                sess.expires_at = now + SESSION_TTL
                db.commit()
            principal.authenticated, principal.method, principal.session_id = True, "session", sess.id
    return principal


def allowed(principal: Principal, method: str, path: str) -> bool:
    """Whether the caller may make this /api request at all."""
    if not principal.enabled or principal.authenticated:
        return True
    if (method, path) in OPEN_ROUTES or method == "OPTIONS":
        return True
    if principal.guest_access != "read" or method not in SAFE_METHODS:
        return False
    return not (path.startswith(SENSITIVE_PREFIXES) or is_page_export(path))


# ---------- sessions and tokens ----------
def prune_sessions(db: Session) -> None:
    now = _now()
    db.query(models.AuthSession).filter(
        (models.AuthSession.expires_at <= now) | (models.AuthSession.created_at <= now - SESSION_MAX_AGE)
    ).delete()
    db.commit()


def create_session(db: Session) -> str:
    prune_sessions(db)
    token = secrets.token_urlsafe(32)
    now = _now()
    db.add(models.AuthSession(token_hash=_sha(token), created_at=now, expires_at=now + SESSION_TTL, last_seen=now))
    db.commit()
    return token


def revoke_sessions(db: Session) -> int:
    n = db.query(models.AuthSession).delete()
    db.commit()
    return n


def revoke_tokens(db: Session) -> int:
    n = db.query(models.ApiToken).delete()
    db.commit()
    return n


def create_token(db: Session, name: str) -> tuple[models.ApiToken, str]:
    token = "sp_" + secrets.token_urlsafe(32)
    row = models.ApiToken(name=name, token_hash=_sha(token), created_at=_now())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, token


def drop_session_by_cookie(db: Session, cookie: str) -> None:
    db.query(models.AuthSession).filter_by(token_hash=_sha(cookie)).delete()
    db.commit()


# ---------- login throttle (in memory; the container runs one worker) ----------
FREE_FAILURES = 5  # attempts per address before the wait starts
MAX_BACKOFF = 60  # seconds: the per-address wait never grows past this
MAX_ENTRIES = 10_000  # addresses remembered; the oldest is evicted past this
GLOBAL_LIMIT = 30  # attempts across all addresses within GLOBAL_WINDOW before logins are slowed
GLOBAL_WINDOW = 60.0
GLOBAL_DELAY = 5  # then one attempt per this many seconds, for everyone
IDLE_FORGET = 3600.0  # an unblocked address untouched this long is forgotten
WARN_EVERY = 60.0
_clock = time.monotonic
_lock = threading.Lock()
_fails: dict[str, list] = {}  # key -> [attempts, blocked until, last attempt]; oldest first
_recent: deque = deque()  # attempt times within GLOBAL_WINDOW, all addresses
_state = {"global_next": 0.0, "last_purge": 0.0, "last_warn": None}


def throttle_key(ip: str) -> str:
    """IPv4 by full address (IPv4-mapped IPv6 counts as IPv4), IPv6 by its /64, so one host
    cannot dodge the limit by rotating addresses inside its own prefix."""
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return ip or ""
    addr = getattr(addr, "ipv4_mapped", None) or addr
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def _warn(now: float, what: str) -> None:
    last = _state["last_warn"]
    if last is not None and now - last < WARN_EVERY:
        return
    _state["last_warn"] = now
    log.warning(
        "Login throttle engaged (%s). If this app runs behind a reverse proxy, set STARTPAGE_TRUSTED_PROXIES "
        "to the proxy's address so each visitor is throttled separately instead of all sharing the proxy's.",
        what,
    )


def _wait_for(until: float, now: float) -> int:
    return max(1, math.ceil(until - now))


def throttle_reserve(ip: str) -> int:
    """Check and record one login attempt in a single step, before the password is hashed.

    Returns 0 when the attempt may go ahead (it is now counted as a failure) or the seconds to
    wait. An attempt refused here is NOT counted, so retrying while blocked never extends the block.
    Call throttle_release() when the attempt turns out to be correct."""
    key = throttle_key(ip)
    with _lock:
        now = _clock()
        rec = _fails.get(key)
        if rec and now < rec[1]:
            _warn(now, "address blocked")
            return _wait_for(rec[1], now)
        while _recent and now - _recent[0] > GLOBAL_WINDOW:
            _recent.popleft()
        if len(_recent) >= GLOBAL_LIMIT and now < _state["global_next"]:
            _warn(now, "global budget")
            return _wait_for(_state["global_next"], now)
        _recent.append(now)
        if len(_recent) >= GLOBAL_LIMIT:
            _state["global_next"] = now + GLOBAL_DELAY  # over budget: one attempt per GLOBAL_DELAY for everyone
        if rec is None:
            if len(_fails) >= MAX_ENTRIES:
                if now - _state["last_purge"] > 60:
                    _state["last_purge"] = now
                    for k in [k for k, v in _fails.items() if now - v[2] > IDLE_FORGET and now >= v[1]]:
                        del _fails[k]
                while len(_fails) >= MAX_ENTRIES:
                    del _fails[next(iter(_fails))]
            rec = [0, 0.0, now]
        else:
            del _fails[key]  # re-insert at the end: _fails stays ordered by last use
        _fails[key] = rec
        rec[0] += 1
        rec[2] = now
        if rec[0] >= FREE_FAILURES:
            rec[1] = now + min(MAX_BACKOFF, 2 ** min(rec[0] - FREE_FAILURES + 1, 16))  # 2 s, 4 s, 8 s ... 60 s
        return 0


def throttle_release(ip: str) -> None:
    """A correct password: forget this address and give back the attempt reserved for it."""
    with _lock:
        _fails.pop(throttle_key(ip), None)
        if _recent:
            _recent.pop()


def reset_throttle() -> None:
    with _lock:
        _fails.clear()
        _recent.clear()
        _state.update(global_next=0.0, last_purge=0.0, last_warn=None)


# ---------- recovery CLI ----------
def reset(db: Session) -> dict:
    """Remove the password, every session and every API token. Guest mode goes back to "none"."""
    had_password = bool(get_config(db)["password_hash"])
    row = db.get(models.AppSetting, KEY)
    if row:
        db.delete(row)
    sessions = db.query(models.AuthSession).delete()
    tokens = db.query(models.ApiToken).delete()
    db.commit()
    return {"password_removed": had_password, "sessions_removed": sessions, "tokens_removed": tokens}


def main(argv: list[str]) -> int:
    if argv != ["reset"]:
        print("usage: python -m app.auth reset   (run from backend/; removes password, sessions, API tokens)")
        return 2
    from .database import DB_PATH, Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        result = reset(db)
    print(f"Database: {DB_PATH}")
    print("Password removed." if result["password_removed"] else "No password was set.")
    print(f"Sessions removed: {result['sessions_removed']}")
    print(f"API tokens removed: {result['tokens_removed']}")
    print("Authentication is now off. Restart is not needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
