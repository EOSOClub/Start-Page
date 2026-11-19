import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

HEALTH_CHECK_INDEX = "ix_health_checks_service_checked"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Dashboard(Base):
    __tablename__ = "dashboards"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0)
    columns: Mapped[int] = mapped_column(Integer, default=24)
    row_height: Mapped[int] = mapped_column(Integer, default=40)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    widgets: Mapped[list["Widget"]] = relationship(
        back_populates="dashboard", cascade="all, delete-orphan", order_by="Widget.y"
    )


class Service(Base):
    """What a thing *is* - independent of where it is shown."""

    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    protocol: Mapped[str] = mapped_column(String, default="http")
    host: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str] = mapped_column(String, default="")
    icon: Mapped[str] = mapped_column(String, default="")
    color: Mapped[str] = mapped_column(String, default="")
    category: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    health_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_type: Mapped[str] = mapped_column(String, default="http")  # http | tcp | ping
    health_url: Mapped[str] = mapped_column(String, default="")  # override; default = url
    health_interval: Mapped[int] = mapped_column(Integer, default=30)
    depends_on: Mapped[list] = mapped_column(JSON, default=list)  # parent service ids
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    @property
    def url(self) -> str:
        """The link. Defensive on purpose: a row with a protocol other than http/https (written by an
        older version or by hand) gives "" rather than a javascript:// style link."""
        protocol = (self.protocol or "").strip().lower()
        host = (self.host or "").strip()
        if protocol not in ("http", "https") or not host:
            return ""
        default_port = {"http": 80, "https": 443}[protocol]
        port = "" if (self.port is None or self.port == default_port) else f":{self.port}"
        path = self.path or ""
        if path and not path.startswith("/"):
            path = "/" + path
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"  # IPv6
        return f"{protocol}://{host}{port}{path}"


class Widget(Base):
    """Where something goes on a dashboard, and how it is displayed."""

    __tablename__ = "widgets"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    dashboard_id: Mapped[str] = mapped_column(ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    x: Mapped[float] = mapped_column(Float, default=0)
    y: Mapped[float] = mapped_column(Float, default=0)
    w: Mapped[float] = mapped_column(Float, default=4)
    h: Mapped[float] = mapped_column(Float, default=3)
    service_id: Mapped[str | None] = mapped_column(ForeignKey("services.id", ondelete="SET NULL"), nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    # Optional phone placement {x, y, w, h, hidden}, in cells of the page's `columns_sm` grid.
    # NULL = no phone position (the widget is appended automatically on phones).
    layout_sm: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)

    dashboard: Mapped[Dashboard] = relationship(back_populates="widgets")
    service: Mapped[Service | None] = relationship()


class Integration(Base):
    """External data source polled by the backend (Docker, Uptime Kuma, JSON endpoint...)."""

    __tablename__ = "integrations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # a key of integrations.SPECS (app/integrations/types/)
    interval: Mapped[int] = mapped_column(Integer, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class HealthCheck(Base):
    """One completed health check (online / degraded / offline). Pruned after `health_history_days`."""

    __tablename__ = "health_checks"
    # Latest-row lookups and history ranges are per service. Existing databases get the same
    # index from main._migrate() (create_all does not add indexes to existing tables).
    __table_args__ = (Index(HEALTH_CHECK_INDEX, "service_id", "checked_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)  # naive UTC
    status: Mapped[str] = mapped_column(String, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class HealthEvent(Base):
    """One state transition of a service (the incident log). Pruned after `health_events_days`."""

    __tablename__ = "health_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_id: Mapped[str] = mapped_column(
        ForeignKey("services.id", ondelete="CASCADE"), nullable=False, index=True
    )
    at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)  # naive UTC
    from_status: Mapped[str] = mapped_column(String, nullable=False)
    to_status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[str | None] = mapped_column(String, nullable=True)


class HealthAlertState(Base):
    """Notification state per service, so a restart neither repeats a "down" alert nor loses a recovery."""

    __tablename__ = "health_alert_state"

    service_id: Mapped[str] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"), primary_key=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)  # consecutive failed checks
    down_notified: Mapped[bool] = mapped_column(Boolean, default=False)  # "down" was delivered
    pending_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # threshold hit, not yet delivered


class AuthSession(Base):
    """A browser login. Only the sha256 of the cookie token is stored."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # naive UTC
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ApiToken(Base):
    """A bearer token for scripts and the MCP server. Only the sha256 is stored."""

    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # naive UTC
    last_used: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AppSetting(Base):
    """Single-row key/value store for global UI settings."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
