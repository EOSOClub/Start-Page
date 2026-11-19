"""Building blocks every integration type uses: the spec, the fetch context, the normalised
result shape, and config validation / secret handling. See the package docstring for the recipe."""
import asyncio
import logging
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional, Union
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..schemas import MIN_INTERVAL, SECRET_MASK
from .errors import AuthFailed, IntegrationError, host_of

HTTP_TIMEOUT = 8.0  # seconds per request (connect, read, ...) on the shared client
FIELD_TYPES = {"text", "url", "number", "checkbox", "select", "textarea", "secret", "headers"}
SECRET_TYPES = {"text", "secret", "textarea", "headers"}  # what may carry `secret: True`
CAPABILITIES = {"stats", "items"}
_TYPE_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_MAX_TEXT = 2048
_MAX_AREA = 10_000

VERIFY_TLS_FIELD = {
    "key": "verify_tls",
    "label": "Verify TLS certificate",
    "type": "checkbox",
    "default": True,
    "help": "On by default. For a self-signed or private-CA host, paste its CA below or turn this off.",
}
CA_CERT_FIELD = {
    "key": "ca_cert",
    "label": "Trusted CA certificate (PEM)",
    "type": "textarea",
    "rows": 4,
    "max_length": 20_000,
    "placeholder": "-----BEGIN CERTIFICATE-----",
    "help": "Optional. A CA or self-signed certificate to trust for this integration, in addition to the "
            "system's. Certificates only, never a private key. Ignored when verification is off.",
}


def ca_cert_error(pem: Any) -> Optional[str]:
    """Why `pem` cannot be used as a trusted CA, or None. Never echoes the value."""
    if not isinstance(pem, str) or "-----BEGIN CERTIFICATE-----" not in pem:
        return "must be a PEM certificate (-----BEGIN CERTIFICATE-----)"
    if "PRIVATE KEY" in pem:
        return "contains a private key; paste only the certificate"
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=pem)
    except (ssl.SSLError, ValueError):
        return "is not a valid PEM certificate"
    return None

_log = logging.getLogger("startpage.integrations")
_clock = time.monotonic  # tests replace this to step the auth backoff without sleeping
AUTH_BACKOFF_START = 60.0  # seconds after the first refused login
AUTH_BACKOFF_CAP = 900.0
CLOSE_HOOK_TIMEOUT = 3.0


# ---------------------------------------------------------------- normalised result shape
class Stat(BaseModel):
    """One number or short text for a tile: Queries 12,345; Blocked 17.2 %."""

    key: str
    label: str
    value: Union[int, float, str, bool, None] = None
    unit: Optional[str] = None
    tone: Optional[Literal["ok", "warn", "bad", "info"]] = None
    series: Optional[list[float]] = None  # optional recent history, oldest first, for a sparkline


class Item(BaseModel):
    """One row of a list: a container, a monitor, a download, a feed entry."""

    title: str
    subtitle: Optional[str] = None
    status: Optional[Literal["online", "offline", "degraded", "maintenance", "unknown"]] = None
    progress: Optional[float] = Field(default=None, ge=0, le=100)  # percent
    meta: Optional[str] = None  # short right-aligned text: "12 ms", "2 h ago"
    url: Optional[str] = None
    icon: Optional[str] = None
    # Stable identifiers for row actions (never parsed from the text above): {"node", "vmid", "kind"} for a
    # Proxmox guest, {"endpoint_id", "container_id"} for a Portainer container. Only on rows that an action
    # can address; the widget sends these values back to the action, which validates them again.
    ref: Optional[dict[str, Union[str, int]]] = None


class Normalised(BaseModel):
    """What generic widgets render. A fetch result is a dict that may carry any extra
    type-specific keys (extra="allow") next to these."""

    model_config = ConfigDict(extra="allow")
    stats: list[Stat] = Field(default_factory=list)
    items: list[Item] = Field(default_factory=list)


def check_shape(data: Any, provides: Optional[tuple] = None) -> None:
    """Raise IntegrationError("invalid response") when `stats` / `items` in a result break the shape.
    With `provides` (a spec's capabilities) only those keys are checked: a type that does not provide
    `items` may return an `items` key of its own (the json type passes any payload through)."""
    keys = {"stats", "items"} if provides is None else {"stats", "items"} & set(provides)
    if not isinstance(data, dict) or not (keys & set(data)):
        return
    try:
        Normalised.model_validate({k: v for k, v in data.items() if k in keys})
    except ValueError as exc:  # pydantic.ValidationError is a ValueError
        raise IntegrationError("invalid response (stats/items do not match the normalised shape)") from exc


def stat(key: str, label: str, value: Any = None, unit: Optional[str] = None, tone: Optional[str] = None,
         series: Optional[list] = None) -> dict:
    """A stat dict with unset optional keys left out."""
    out: dict[str, Any] = {"key": key, "label": label, "value": value}
    if unit:
        out["unit"] = unit
    if tone:
        out["tone"] = tone
    if series:
        out["series"] = series
    return out


# ---------------------------------------------------------------- spec
@dataclass
class Action:
    """A write operation a widget or tool may trigger: `fn(ctx, payload)`. `payload` lists
    fields (same vocabulary as spec fields); the payload is validated before `fn` runs."""

    fn: Callable[["Context", dict], Awaitable[Any]]
    label: str
    description: str = ""
    payload: list[dict] = field(default_factory=list)


@dataclass
class Spec:
    type: str
    label: str
    description: str  # one line, shown in the type picker
    fetch: Callable[["Context"], Awaitable[Any]]
    fields: list[dict] = field(default_factory=list)
    default_interval: int = 30
    provides: tuple[str, ...] = ()  # "stats" and/or "items": which normalised keys fetch returns
    actions: dict[str, Action] = field(default_factory=dict)
    uses_http: bool = True  # False: no HTTP client, no `verify_tls` field (e.g. host)
    client_factory: Optional[Callable[["Context"], httpx.AsyncClient]] = None  # default: plain client
    validate: Optional[Callable[[dict], list[tuple[str, str]]]] = None  # cross-field check -> [(key, message)]
    close: Optional[Callable[["Context"], Awaitable[Any]]] = None  # called once when a context is closed (log out)
    secret_url: bool = False  # the URL path is itself a credential: logs keep the host only

    def __post_init__(self) -> None:
        if not _TYPE_KEY.match(self.type or ""):
            raise ValueError(f"integration type key {self.type!r} must match [a-z][a-z0-9_]*")
        if not self.label or not self.description or "\n" in self.description:
            raise ValueError(f"{self.type}: label and a one-line description are required")
        if self.default_interval < MIN_INTERVAL:
            raise ValueError(f"{self.type}: default_interval must be >= {MIN_INTERVAL}")
        if set(self.provides) - CAPABILITIES:
            raise ValueError(f"{self.type}: provides must be a subset of {sorted(CAPABILITIES)}")
        self.fields = [dict(f, secret=True) if f.get("type") == "secret" else dict(f) for f in self.fields]  # type secret = write-only
        if self.uses_http and not any(f.get("key") == "verify_tls" for f in self.fields):
            self.fields.append(dict(VERIFY_TLS_FIELD))
        if self.uses_http and not any(f.get("key") == "ca_cert" for f in self.fields):
            self.fields.append(dict(CA_CERT_FIELD))
        _check_field_defs(self.type, self.fields)
        for name, action in self.actions.items():
            if not _TYPE_KEY.match(name):
                raise ValueError(f"{self.type}: action name {name!r} must match [a-z][a-z0-9_]*")
            action.payload = [dict(f, secret=True) if f.get("type") == "secret" else dict(f) for f in action.payload]
            _check_field_defs(f"{self.type}.{name}", action.payload)

    @property
    def secret_keys(self) -> list[str]:
        return [f["key"] for f in self.fields if f.get("secret")]

    @property
    def defaults(self) -> dict:
        return {f["key"]: f["default"] for f in self.fields if "default" in f}

    def public(self) -> dict:
        """Everything about the type a client needs, with no callables (served by GET /types)."""
        def pub(f: dict) -> dict:
            return {**f, "mask": SECRET_MASK} if f.get("secret") else dict(f)

        return {
            "type": self.type,
            "label": self.label,
            "description": self.description,
            "default_interval": self.default_interval,
            "provides": list(self.provides),
            "fields": [pub(f) for f in self.fields],
            "actions": [
                {"name": n, "label": a.label, "description": a.description, "payload": [pub(f) for f in a.payload]}
                for n, a in self.actions.items()
            ],
        }


def _check_field_defs(where: str, fields: list[dict]) -> None:
    seen: set[str] = set()
    for f in fields:
        key = f.get("key")
        if not key or not isinstance(key, str) or key in seen:
            raise ValueError(f"{where}: field key {key!r} is missing or duplicated")
        seen.add(key)
        if f.get("type") not in FIELD_TYPES:
            raise ValueError(f"{where}.{key}: unknown field type {f.get('type')!r}; use one of {sorted(FIELD_TYPES)}")
        if not f.get("label"):
            raise ValueError(f"{where}.{key}: label is required")
        if f.get("secret") and f["type"] not in SECRET_TYPES:
            raise ValueError(f"{where}.{key}: only {sorted(SECRET_TYPES)} fields can be secret")
        if f["type"] == "select" and not f.get("options"):
            raise ValueError(f"{where}.{key}: select needs options")


# ---------------------------------------------------------------- context handed to fetch/actions
class Context:
    """What `fetch(ctx)` and actions receive.

    cfg       the integration's config with the spec's field defaults filled in
    client    one shared httpx.AsyncClient per integration (timeout, verify from `verify_tls`)
    state     a dict that survives between polls (session ids, tokens); reset when the config changes
    base_url(key="url") / url(path, key="url")   URL helpers (trailing slash and missing scheme tolerated)
    """

    def __init__(self, spec: Spec, cfg: Optional[dict] = None, integration_id: str = "", state: Optional[dict] = None):
        self.spec = spec
        self.integration_id = integration_id
        self.cfg: dict = {**spec.defaults, **{k: v for k, v in _strip_cfg(spec, cfg or {}).items() if v is not None and v != ""}}
        self.retired = False  # replaced or deleted: queued fetches must not start on it
        self.closed = False  # client closed: no new client may be built
        self.manual = False  # a person triggered this call (Test / refresh / action): auth backoff is bypassed
        self.status_auth_failures = 0  # consecutive polls answered 401/403 (static keys); stretches the interval
        self._auth_failures = 0
        self._auth_until = 0.0
        self.state: dict = state if state is not None else {}
        self.lock = asyncio.Lock()  # one fetch per integration at a time (see runtime.fetch)
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def verify_tls(self) -> bool:
        return bool(self.cfg.get("verify_tls", True))

    @property
    def tls_verify(self) -> Union[bool, ssl.SSLContext]:
        """What to pass as httpx `verify`: False, True, or a context that also trusts `ca_cert`."""
        if not self.verify_tls:
            return False
        pem = self.cfg.get("ca_cert")
        if not isinstance(pem, str) or not pem.strip():
            return True
        if ca_cert_error(pem):  # a row stored before validation, or edited by hand
            raise IntegrationError("the trusted CA certificate is not valid PEM")
        context = ssl.create_default_context()  # system CAs, hostname check on
        context.load_verify_locations(cadata=pem)
        return context

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            if self.closed:
                raise IntegrationError("integration was changed or removed")
            if self.spec.client_factory:
                self._client = self.spec.client_factory(self)
            else:
                self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT, verify=self.tls_verify)
        return self._client

    async def aclose(self) -> None:
        if self.closed:
            return
        if self.spec.close is not None and self._client is not None:  # log out of the upstream session
            try:
                await asyncio.wait_for(self.spec.close(self), CLOSE_HOOK_TIMEOUT)
            except Exception:  # noqa: BLE001
                _log.debug("close hook of %s failed", self.spec.type, exc_info=True)
        self.closed = True
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    # ---- auth backoff: a wrong password must not cause a login attempt on every poll
    def auth_gate(self, message: str) -> None:
        """Call before logging in: raises the same safe error WITHOUT a request while a backoff runs.
        Calls triggered by a person (`manual`) are never held back."""
        if not self.manual and self._auth_until > _clock():
            raise AuthFailed(message)

    def auth_fail(self, message: str) -> AuthFailed:
        """Record a refused login (delay 60 s, doubling, at most 15 min) and return the error to raise."""
        self._auth_failures += 1
        self._auth_until = _clock() + min(AUTH_BACKOFF_START * 2 ** (self._auth_failures - 1), AUTH_BACKOFF_CAP)
        return AuthFailed(message)

    def auth_ok(self) -> None:
        """A fetch worked (the runtime calls this): forget the login backoff."""
        self._auth_failures = 0
        self._auth_until = 0.0

    def raw_url(self, key: str = "url") -> str:
        """The configured URL as typed (a trailing slash is kept: it can matter for a full endpoint
        URL), with http:// added when there is no scheme."""
        raw = str(self.cfg.get(key) or "").strip()
        if not raw:
            raise IntegrationError("URL is not set")
        return raw if _SCHEME.match(raw) else f"http://{raw}"

    def base_url(self, key: str = "url") -> str:
        """raw_url without trailing slashes, for joining paths onto."""
        return self.raw_url(key).rstrip("/")

    def url(self, path: str = "", key: str = "url") -> str:
        return self.base_url(key) + ("/" + path.lstrip("/") if path else "")

    @property
    def host(self) -> str:
        return host_of(self.cfg.get("url"))


def _strip_cfg(spec: Spec, cfg: dict) -> dict:
    """Surrounding whitespace is never part of a secret or a header (rows stored before validation
    stripped them may hold a pasted trailing newline, which httpx refuses to send)."""
    out = dict(cfg)
    for f in spec.fields:
        v = out.get(f["key"])
        if isinstance(v, str) and (f.get("secret") or f["type"] in ("text", "url")):
            out[f["key"]] = v.strip()
        elif f["type"] == "headers" and isinstance(v, dict):
            out[f["key"]] = {str(k).strip(): (x.strip() if isinstance(x, str) else x) for k, x in v.items()}
    return out


async def cached(ctx: Context, key: str, ttl: float, loader: Callable[[], Awaitable[Any]]) -> Any:
    """`await loader()` at most every `ttl` seconds per context; in between the last value is returned
    (for expensive upstream calls whose answer changes slowly, e.g. a library size)."""
    hit = ctx.state.get(key)
    if hit is not None and _clock() - hit[1] < ttl:
        return hit[0]
    value = await loader()
    ctx.state[key] = (value, _clock())
    return value


def default_client(ctx: Context) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT, verify=ctx.tls_verify)


# ---------------------------------------------------------------- config validation and secrets
def is_set(value: Any) -> bool:
    return value not in (None, "", {}, [])


def merge_secrets(fields: list[dict], incoming: dict, stored: Optional[dict]) -> dict:
    """The config to validate and save. Secret keys follow the write-only rules:
    omitted or the mask -> the stored value (nothing when there is none, so the mask is never
    stored); "" or null -> cleared; anything else -> the new value. Other keys are taken as sent."""
    stored = stored or {}
    out = {k: v for k, v in (incoming or {}).items()}
    for f in fields:
        if not f.get("secret"):
            continue
        k = f["key"]
        if k not in out or out[k] == SECRET_MASK:
            if is_set(stored.get(k)) and stored.get(k) != SECRET_MASK:
                out[k] = stored[k]
            else:
                out.pop(k, None)
        elif out[k] is None or out[k] == "":
            out.pop(k)
    return out


def public_config(spec: Optional[Spec], cfg: Optional[dict]) -> dict:
    """Config as the API shows it: set secrets are the mask, unset secrets are "". A type that
    is no longer registered shows every value masked."""
    cfg = dict(cfg or {})
    if spec is None:
        return {k: SECRET_MASK for k in cfg}
    for k in spec.secret_keys:
        cfg[k] = SECRET_MASK if is_set(cfg.get(k)) else ""
    return cfg


def _check_url(v: Any, schemes: list[str]) -> Optional[str]:
    if not isinstance(v, str):
        return "must be text"
    v = v.strip()
    m = _SCHEME.match(v)
    if m:
        scheme = m.group(0)[:-3].lower()
        if scheme not in schemes:
            return "must start with " + " or ".join(f"{s}://" for s in schemes)
        if scheme == "unix":
            return None if len(v) > len("unix://") else "needs a socket path"
        rest = v
    else:
        rest = "http://" + v
    try:
        u = urlsplit(rest)
        if not u.hostname:
            return "needs a host name"
        u.port  # noqa: B018  (raises ValueError on a bad port)
    except ValueError:
        return "is not a valid URL"
    return None


def _has_control(text: str) -> bool:
    return any(ord(c) < 32 or ord(c) == 127 for c in text)


def _check_one(f: dict, v: Any) -> Optional[str]:
    t = f["type"]
    if t == "checkbox":
        return None if isinstance(v, bool) else "must be true or false"
    if t == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return "must be a number"
        if v != v or v in (float("inf"), float("-inf")):
            return "must be a finite number"
        if "min" in f and v < f["min"]:
            return f"must be at least {f['min']}"
        if "max" in f and v > f["max"]:
            return f"must be at most {f['max']}"
        return None
    if t == "select":
        allowed = [o["value"] if isinstance(o, dict) else o for o in f["options"]]
        return None if v in allowed else "must be one of " + ", ".join(str(a) for a in allowed)
    if t == "headers":
        if not isinstance(v, dict) or len(v) > 50:
            return "must be an object of header names to values (at most 50)"
        for name, value in v.items():
            if not isinstance(name, str) or not _HEADER_NAME.match(name.strip()):
                return "has an invalid header name"
            if not isinstance(value, str) or len(value) > _MAX_TEXT or _has_control(value.strip()):
                return f"has an invalid value for header {name.strip()!r} (no control characters)"
        return None
    if not isinstance(v, str):
        return "must be text"
    if len(v) > f.get("max_length", _MAX_AREA if t == "textarea" else _MAX_TEXT):
        return "is too long"
    if t != "textarea" and _has_control(v.strip()):
        return "contains control characters (check for a pasted line break or tab)"
    if t == "url":
        return _check_url(v, f.get("schemes") or ["http", "https"])
    if f.get("pattern") and not re.fullmatch(f["pattern"], v):
        return "has an unsupported format"
    return None


def check_values(fields: list[dict], values: dict, *, strict_keys: bool = True) -> tuple[dict, list[tuple[str, str]]]:
    """(clean values, [(key, message)]): required fields present, types and options right.
    Unset / empty values are dropped; unknown keys are errors when `strict_keys`."""
    known = {f["key"]: f for f in fields}
    errors: list[tuple[str, str]] = []
    clean: dict[str, Any] = {}
    if strict_keys:
        errors += [(k, "unknown field") for k in values if k not in known]
    for f in fields:
        k = f["key"]
        v = values.get(k)
        if v is None or v == "" or (f["type"] == "headers" and v == {}):
            if f.get("required"):
                errors.append((k, "is required"))
            continue
        err = _check_one(f, v)
        if err:
            errors.append((k, err))
        else:
            if isinstance(v, str) and (f.get("secret") or f["type"] in ("text", "url")):
                v = v.strip()
            elif f["type"] == "headers":
                v = {n.strip(): x.strip() for n, x in v.items()}
            if v == "":  # only whitespace was typed: unset
                if f.get("required"):
                    errors.append((k, "is required"))
                continue
            clean[k] = v
    return clean, errors


def validate_config(spec: Spec, incoming: dict, stored: Optional[dict] = None) -> tuple[dict, list[tuple[str, str]]]:
    """Resolve secrets against `stored`, then validate. Returns (config to save, errors)."""
    merged = merge_secrets(spec.fields, incoming, stored)
    clean, errors = check_values(spec.fields, merged)
    if not errors and clean.get("ca_cert"):
        problem = ca_cert_error(clean["ca_cert"])
        if problem:
            errors = [("ca_cert", problem)]
    if not errors and spec.validate:
        errors = spec.validate({**spec.defaults, **clean})
    return clean, errors


# ---------------------------------------------------------------- sample for /test
def trim_sample(value: Any, depth: int = 0) -> Any:
    """A short JSON-safe preview of a fetch result: first rows, short strings, shallow."""
    if isinstance(value, str):
        return value if len(value) <= 120 else value[:120] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if depth >= 3:
        return "…"
    if isinstance(value, dict):
        items = list(value.items())
        out = {str(k): trim_sample(v, depth + 1) for k, v in items[:12]}
        if len(items) > 12:
            out["…"] = f"{len(items) - 12} more keys"
        return out
    if isinstance(value, (list, tuple)):
        out = [trim_sample(v, depth + 1) for v in value[:5]]
        if len(value) > 5:
            out.append(f"… {len(value) - 5} more")
        return out
    return str(value)[:120]
