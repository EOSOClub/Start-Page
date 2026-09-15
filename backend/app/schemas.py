from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


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
class WidgetBase(BaseModel):
    type: str
    x: float = 0
    y: float = 0
    w: float = 4
    h: float = 3
    service_id: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)


class WidgetCreate(WidgetBase):
    id: Optional[str] = Field(default=None, min_length=1)


class WidgetUpdate(BaseModel):
    dashboard_id: Optional[str] = None  # move to another page
    type: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    service_id: Optional[str] = None
    config: Optional[dict[str, Any]] = None


class WidgetOut(WidgetBase):
    model_config = ConfigDict(from_attributes=True)
    id: str
    dashboard_id: str


class LayoutItem(BaseModel):
    id: str
    x: float
    y: float
    w: float
    h: float


# ---------- Dashboards ----------
class DashboardBase(BaseModel):
    name: str
    position: int = 0
    columns: int = 24
    row_height: int = 40
    settings: dict[str, Any] = Field(default_factory=dict)


class DashboardCreate(DashboardBase):
    id: Optional[str] = Field(default=None, min_length=1)


class DashboardUpdate(BaseModel):
    name: Optional[str] = None
    position: Optional[int] = None
    columns: Optional[int] = None
    row_height: Optional[int] = None
    settings: Optional[dict[str, Any]] = None


class DashboardOut(DashboardBase):
    model_config = ConfigDict(from_attributes=True)
    id: str


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


# ---------- Integrations ----------
class IntegrationBase(BaseModel):
    name: str
    type: str
    interval: int = 30
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class IntegrationCreate(IntegrationBase):
    id: Optional[str] = Field(default=None, min_length=1)


class IntegrationUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    interval: Optional[int] = None
    enabled: Optional[bool] = None
    config: Optional[dict[str, Any]] = None


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


# ---------- Import / Export ----------
class ExportBundle(BaseModel):
    version: int = 2
    services: list[ServiceOut]
    dashboards: list[DashboardDetail]
    integrations: list[IntegrationOut] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
