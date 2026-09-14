import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


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
        default_port = {"http": 80, "https": 443}.get(self.protocol)
        port = "" if (self.port is None or self.port == default_port) else f":{self.port}"
        path = self.path or ""
        if path and not path.startswith("/"):
            path = "/" + path
        return f"{self.protocol}://{self.host}{port}{path}"


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

    dashboard: Mapped[Dashboard] = relationship(back_populates="widgets")
    service: Mapped[Service | None] = relationship()


class Integration(Base):
    """External data source polled by the backend (Docker, Uptime Kuma, JSON endpoint...)."""

    __tablename__ = "integrations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)  # docker | uptime_kuma | json
    interval: Mapped[int] = mapped_column(Integer, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    """Single-row key/value store for global UI settings."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
