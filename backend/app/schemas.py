import logging
import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def patch_data(payload: BaseModel, nullable: tuple[str, ...] = ()) -> dict[str, Any]:
    """Fields a PATCH actually sets. An explicit null only counts for `nullable` fields;
    anywhere else it would store NULL / JSON null in a column the Out models require."""
    return {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None or k in nullable}


# ---------- Services ----------
class ServiceBase(BaseModel):
    name: str
    protocol: str = "http"
    host: str
    port: Optional[int] = None
    path: str = ""
    icon: str = ""
    color: str = ""
    category: str = ""
    description: str = ""
    health_enabled: bool = True
    health_type: str = "http"
    health_url: str = ""
    health_interval: int = 30
    depends_on: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ServiceCreate(ServiceBase):
    id: Optional[str] = Field(default=None, min_length=1)


class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    protocol: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    path: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    health_enabled: Optional[bool] = None
    health_type: Optional[str] = None
    health_url: Optional[str] = None
    health_interval: Optional[int] = None
    depends_on: Optional[list[str]] = None
    metadata: Optional[dict[str, Any]] = None


class ServiceOut(ServiceBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    url: str


# ---------- Widgets ----------
log = logging.getLogger("startpage.layout")
X_MAX, Y_MAX = 1000, 100_000  # caps for x / w and for y / h, in grid cells
_warned: set[str] = set()


def _warn_once(what: str) -> None:
    if what not in _warned and len(_warned) < 200:
        _warned.add(what)
        log.warning("Stored layout value is not valid and was clamped or ignored on read: %s", what)


class PhoneLayout(BaseModel):
    """A widget's position on the page's phone grid (`columns_sm` columns, same row height)."""

    x: float = Field(default=0, ge=0, le=X_MAX, allow_inf_nan=False)
    y: float = Field(default=0, ge=0, le=Y_MAX, allow_inf_nan=False)
    w: float = Field(default=2, gt=0, le=X_MAX, allow_inf_nan=False)
    h: float = Field(default=2, gt=0, le=Y_MAX, allow_inf_nan=False)
    hidden: bool = False


def fit_error(x: float, w: float, cols: int, what: str = "phone") -> Optional[str]:
    """Why x + w does not fit a grid of `cols` columns, or None."""
    if x + w > cols + 1e-6:
        return f"x + w must fit the {what} grid ({cols} columns), got x {x:g} + w {w:g}"
    return None


def _num(v: Any, lo: float, hi: float, default: float, positive: bool = False) -> float:
    """A finite number clamped into [lo, hi]; the default for anything else (None, NaN, text)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or (positive and v <= 0):
        _warn_once(f"{v!r}")
        return default
    if v < lo or v > hi:
        _warn_once(f"{v!r}")
    return min(max(float(v), lo), hi)


def sanitize_layout_sm(v: Any) -> Optional[dict]:
    """A stored phone layout made safe to output: clamped when it is a placement with odd numbers,
    None when it is not a placement at all."""
    if v is None:
        return None
    if not isinstance(v, dict) or not all(
        isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool) and math.isfinite(v.get(k)) for k in ("x", "y", "w", "h")
    ):
        _warn_once(f"layout_sm {str(v)[:60]!r}")
        return None
    return {
        "x": _num(v["x"], 0, X_MAX, 0), "y": _num(v["y"], 0, Y_MAX, 0),
        "w": _num(v["w"], 0.01, X_MAX, 2, positive=True), "h": _num(v["h"], 0.01, Y_MAX, 2, positive=True),
        "hidden": bool(v.get("hidden")),
    }


class WidgetBase(BaseModel):
    type: str
    x: float = 0
    y: float = 0
    w: float = 4
    h: float = 3
    service_id: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    layout_sm: Optional[PhoneLayout] = None  # None = no phone position (appended automatically)


_Cx = Field(default=0, ge=0, le=X_MAX, allow_inf_nan=False)
_Cy = Field(default=0, ge=0, le=Y_MAX, allow_inf_nan=False)


class WidgetCreate(WidgetBase):
    """What a write accepts: finite numbers within the caps (422 otherwise)."""

    id: Optional[str] = Field(default=None, min_length=1)
    x: float = _Cx
    y: float = _Cy
    w: float = Field(default=4, gt=0, le=X_MAX, allow_inf_nan=False)
    h: float = Field(default=3, gt=0, le=Y_MAX, allow_inf_nan=False)


class WidgetUpdate(BaseModel):
    dashboard_id: Optional[str] = None  # move to another page
    type: Optional[str] = None
    x: Optional[float] = Field(default=None, ge=0, le=X_MAX, allow_inf_nan=False)
    y: Optional[float] = Field(default=None, ge=0, le=Y_MAX, allow_inf_nan=False)
    w: Optional[float] = Field(default=None, gt=0, le=X_MAX, allow_inf_nan=False)
    h: Optional[float] = Field(default=None, gt=0, le=Y_MAX, allow_inf_nan=False)
    service_id: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    layout_sm: Optional[PhoneLayout] = None  # an explicit null removes the phone position


class WidgetOut(WidgetBase):
    """Reads never fail on a stored row with odd numbers (a database written by an older version or
    edited by hand): the values are clamped, or the phone layout dropped, and logged once."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    dashboard_id: str

    @field_validator("x", "y", mode="before")
    @classmethod
    def _pos(cls, v, info):
        return _num(v, 0, X_MAX if info.field_name == "x" else Y_MAX, 0)

    @field_validator("w", "h", mode="before")
    @classmethod
    def _size(cls, v, info):
        return _num(v, 0.01, X_MAX if info.field_name == "w" else Y_MAX, 4 if info.field_name == "w" else 3, positive=True)

    @field_validator("layout_sm", mode="before")
    @classmethod
    def _phone(cls, v):
        if isinstance(v, BaseModel):
            return v
        return sanitize_layout_sm(v)


class LayoutItem(BaseModel):
    id: str
    x: float = Field(ge=0, le=X_MAX, allow_inf_nan=False)
    y: float = Field(ge=0, le=Y_MAX, allow_inf_nan=False)
    w: float = Field(gt=0, le=X_MAX, allow_inf_nan=False)
    h: float = Field(gt=0, le=Y_MAX, allow_inf_nan=False)
    hidden: Optional[bool] = None  # phone layout only; None keeps the stored value


# ---------- Dashboards ----------
PHONE_COLS_DEFAULT = 4
PHONE_COLS_MAX = 12
PHONE_LAYOUTS = ("auto", "custom")


def phone_columns(settings: Optional[dict]) -> int:
    """The page's phone grid width: `columns_sm` when it is a valid integer, else the default."""
    v = (settings or {}).get("columns_sm")
    return v if type(v) is int and 1 <= v <= PHONE_COLS_MAX else PHONE_COLS_DEFAULT


def _check_phone_settings(settings: Optional[dict]) -> Optional[dict]:
    if not settings:
        return settings
    if "columns_sm" in settings:
        v = settings["columns_sm"]
        if type(v) is not int or not 1 <= v <= PHONE_COLS_MAX:
            raise ValueError(f"columns_sm must be a whole number from 1 to {PHONE_COLS_MAX}")
    if "phone_layout" in settings and settings["phone_layout"] not in PHONE_LAYOUTS:
        raise ValueError(f"phone_layout must be one of {list(PHONE_LAYOUTS)}")
    return settings


class DashboardBase(BaseModel):
    name: str
    position: int = 0
    columns: int = 24
    row_height: int = 40
    settings: dict[str, Any] = Field(default_factory=dict)


class DashboardCreate(DashboardBase):
    id: Optional[str] = Field(default=None, min_length=1)

    @field_validator("settings")
    @classmethod
    def _phone_settings(cls, v):
        return _check_phone_settings(v)


class DashboardUpdate(BaseModel):
    name: Optional[str] = None
    position: Optional[int] = None
    columns: Optional[int] = None
    row_height: Optional[int] = None
    settings: Optional[dict[str, Any]] = None

    @field_validator("settings")
    @classmethod
    def _phone_settings(cls, v):
        return _check_phone_settings(v)


class DashboardOut(DashboardBase):
    model_config = ConfigDict(from_attributes=True)
    id: str

    @field_validator("settings", mode="before")
    @classmethod
    def _settings_out(cls, v):
        """Reads never fail on stored settings: an invalid phone setting is left out (the default applies)."""
        if not isinstance(v, dict):
            return {}
        out = dict(v)
        try:
            _check_phone_settings(out)
        except ValueError:
            for key in ("columns_sm", "phone_layout"):
                try:
                    _check_phone_settings({key: out[key]}) if key in out else None
                except ValueError:
                    out.pop(key)
                    _warn_once(f"page setting {key}")
        return out


class DashboardDetail(DashboardOut):
    widgets: list[WidgetOut]


# ---------- Health ----------
class HealthStatus(BaseModel):
    service_id: str
    status: str  # online | offline | unknown | disabled
    http_status: Optional[int] = None
    latency_ms: Optional[float] = None
    checked_at: Optional[float] = None
    error: Optional[str] = None


class HealthBucket(BaseModel):
    t: int  # slice start, unix seconds
    up: int  # online + degraded checks
    total: int  # all checks (0 = gap)
    latency_ms: Optional[float] = None


class HealthHistory(BaseModel):
    service_id: str
    uptime_pct: float
    avg_latency_ms: Optional[float] = None
    checks: int
    buckets: list[HealthBucket]


class HealthEventOut(BaseModel):
    id: int
    service_id: str
    service_name: str
    at: float  # unix seconds
    from_status: str
    to_status: str
    error: Optional[str] = None


class NotifyResult(BaseModel):
    ok: bool
    error: Optional[str] = None


# ---------- Integrations ----------
class IntegrationBase(BaseModel):
    name: str
    type: str
    interval: int = 30
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


MIN_INTERVAL = 5  # seconds between polls; shorter values are raised to this


def _interval(v: Optional[int]) -> Optional[int]:
    """New or edited intervals: 0 and below are a mistake (422), 1-4 s are raised to MIN_INTERVAL.
    Only Create/Update check this, so stored rows and imports with an odd value still load."""
    if v is None:
        return v
    if v <= 0:
        raise ValueError(f"interval must be at least {MIN_INTERVAL} seconds")
    return max(MIN_INTERVAL, v)


class IntegrationCreate(IntegrationBase):
    id: Optional[str] = Field(default=None, min_length=1)
    interval: Optional[int] = None  # None = the type's default_interval

    check_interval = field_validator("interval")(_interval)


class IntegrationUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    interval: Optional[int] = None
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None

    check_interval = field_validator("interval")(_interval)


class IntegrationTest(BaseModel):
    """Run one fetch for an unsaved config. `id` names a stored integration whose saved secrets
    stand in for masked ones (testing an edit)."""

    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    id: Optional[str] = None


class IntegrationTestResult(BaseModel):
    ok: bool
    error: Optional[str] = None
    sample: Any = None


class IntegrationOut(IntegrationBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


class IntegrationData(BaseModel):
    integration_id: str
    ok: bool
    fetched_at: Optional[float] = None
    error: Optional[str] = None
    data: Any = None


# ---------- Settings ----------
NOTIFY_URL_MASK = "********"  # what the API shows for a stored notify_url
SECRET_MASK = NOTIFY_URL_MASK  # the same mask for write-only integration secrets


class Settings(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = "Start Page"
    theme: str = "system"  # system | dark | light
    accent: str = "#6c8cff"
    background_image: str = ""
    background_color: str = ""
    background_blur: int = 0
    background_dim: int = 40
    widget_opacity: int = 100
    font: str = ""
    search_url: str = "https://duckduckgo.com/?q={q}"
    open_new_tab: bool = False
    auto_favicons: bool = True
    backup_enabled: bool = True
    backup_keep: int = 14
    health_history_days: int = Field(default=7, ge=1, le=365)
    health_events_days: int = Field(default=90, ge=1, le=3650)
    notify_type: Literal["none", "ntfy", "discord", "webhook"] = "none"
    notify_url: str = ""
    notify_on_recover: bool = True
    notify_after_failures: int = Field(default=2, ge=1, le=10)

    @field_validator("notify_url")
    @classmethod
    def _notify_url_is_http(cls, v: str) -> str:
        v = v.strip()
        if v == NOTIFY_URL_MASK:
            return v  # "keep the stored URL"; the settings router drops it before saving
        if v and not v.lower().startswith(("http://", "https://")):
            raise ValueError("notify_url must start with http:// or https://")
        return v


class SettingsOut(Settings):
    """What the settings API returns: `notify_url` is write-only (a fixed mask, see routers/settings.py)."""

    notify_url_set: bool = False


# ---------- Import / Export ----------
BUNDLE_VERSION = 3  # 3: integrations verify TLS by default; a row without `verify_tls` in an older bundle means "off"


class ExportBundle(BaseModel):
    version: int = 2  # a bundle without a version is an old one; exports always say BUNDLE_VERSION
    services: list[ServiceOut]
    dashboards: list[DashboardDetail]
    integrations: list[IntegrationOut] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)


class DashboardIn(DashboardCreate):
    """A page inside an imported bundle: held to the same layout rules as a write (finite numbers within
    the caps, valid phone settings), so a bad bundle is refused before anything is written."""

    id: str = Field(min_length=1)
    widgets: list[WidgetCreate] = Field(default_factory=list)


class ImportBundle(ExportBundle):
    """The body of a whole-config import / the content of a backup file."""

    dashboards: list[DashboardIn]


class ImportResult(BaseModel):
    """The response of a whole-config import and of a backup restore: a summary, never the bundle itself
    (it holds integration keys and the notification URL unmasked). `warnings` lists what had to be
    changed to make older data acceptable (e.g. a `javascript` protocol reset to http). Never an error."""

    counts: dict[str, int] = Field(default_factory=dict)  # services, dashboards, widgets, integrations
    warnings: list[str] = Field(default_factory=list)
