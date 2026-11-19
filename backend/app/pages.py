"""Portable single-page format (`startpage-page` v1): export, import, preview, built-in templates.

A page file never contains database ids. Widgets point at services and integrations by a local
`ref` string, and the importer maps refs to ids on the target install:

    {format: "startpage-page", version: 1, name, columns, row_height, settings,
     widgets:      [{type, x, y, w, h, layout_sm?, config, service_ref?}],
     services:     [{ref, name, protocol, host, port, path, icon, color, category, description,
                     health_enabled, health_type, health_url, health_interval, depends_on: [ref]}],
     integrations: [{ref, type, name}]}

Integrations carry only identity (never config or secrets). Which config keys hold service or
integration ids is read from the widget catalog (field types `service`, `services`, `integration`),
so a new widget type needs no code here. The file is untrusted input: everything is validated
before the first write, and the import is one transaction.
"""
import copy
import json
import math
import re
from collections import Counter
from types import SimpleNamespace
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit, urlencode

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from . import discovery, integrations, models, schemas, validation

FORMAT = "startpage-page"
VERSION = 1
MAX_BODY_BYTES = 4 * 1024 * 1024  # a whole request: pages with custom code are a few KB, never MB
MAX_WIDGETS = 500
MAX_SERVICES = 500
MAX_INTEGRATIONS = 50
MAX_STRING = 100_000  # one config string (a custom widget's code is the largest legitimate value)
MAX_CONFIG_BYTES = 500_000  # one widget's whole config as JSON
MAX_DEPTH = 8
MAX_ITEMS = 2000  # entries in one config list / object
MAX_COLUMNS = 96
CATALOG_PATH = Path(__file__).with_name("widget_catalog.json")  # copy of mcp/widget_catalog.json, see catalog.test.js
SERVICE_MODES = ("match", "create", "skip")
CUSTOM_CODE_WARNING = "Contains custom code; it runs sandboxed."

_catalog_cache: Optional[dict] = None


def catalog() -> dict:
    """The widget catalog (type -> {label, group, size, usesService, fields})."""
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return _catalog_cache


def id_fields(widget_type: str) -> dict[str, str]:
    """Config keys of a widget type that hold ids: {key: "service" | "integration"}.
    A value is one id or a list of ids; both are handled wherever it appears."""
    out = {}
    for f in catalog().get(widget_type, {}).get("fields", []):
        if f["type"] in ("service", "services"):
            out[f["key"]] = "service"
        elif f["type"] == "integration":
            out[f["key"]] = "integration"
    return out


def category_fields(widget_type: str) -> list[str]:
    return [f["key"] for f in catalog().get(widget_type, {}).get("fields", []) if f["type"] == "category"]


# ---------- secret-looking content ----------
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s\"'<>`)\]]+")
_SECRET_NAME_RE = re.compile(r"(token|secret|passw|pwd|api[-_]?key|apikey|credential|bearer|signature|access[-_]?key)", re.I)
_SECRET_EXACT = {"key", "sid", "sig", "auth", "authorization", "session", "sessionid"}


def _secret_name(name: str) -> bool:
    return name.lower() in _SECRET_EXACT or bool(_SECRET_NAME_RE.search(name))


_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I)
_TOKEN_LITERAL_RE = re.compile(r"[\"'`]([A-Za-z0-9_\-+/=]{32,})[\"'`]")
_CODE_KEYS = {"html", "css", "js", "code"}


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(c / len(text) * math.log2(c / len(text)) for c in counts.values())


def _looks_like_token(text: str) -> bool:
    """A long random-looking string: letters and digits mixed, high character entropy."""
    return (len(text) >= 20 and any(c.isdigit() for c in text) and any(c.isalpha() for c in text)
            and _entropy(text) >= 3.3)


def _url_problems(url: str) -> list[str]:
    """What looks secret in one URL. Never includes the value itself."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return []
    out = []
    if parts.username or parts.password:
        out.append("credentials in the URL (user:password@)")
    if any(_looks_like_token(seg) for seg in re.split(r"[/;]", parts.path) if len(seg) >= 20):
        out.append("a long token-like value in the URL path")
    for source in (parts.query, parts.fragment):
        for name, _ in parse_qsl(source, keep_blank_values=True):
            if _secret_name(name):
                out.append(f'the secret-looking parameter "{name}"')
    return out


def scan_secrets(value: Any, path: str = "", code: bool = False) -> list[tuple[str, str]]:
    """[(path, reason)] for strings in a config that look like they carry a secret: credentials, secret-looking
    URL parameters, long token-like URL path segments, `Bearer <token>` text, secret-named fields and (in
    custom widget code, `code=True`) long token-like string literals."""
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        text = value[:MAX_STRING]
        for m in _URL_RE.finditer(text):
            for reason in _url_problems(m.group(0)):
                found.append((path, reason))
        if _BEARER_RE.search(text):
            found.append((path, "a Bearer token"))
        if code and any(_looks_like_token(m.group(1)) for m in _TOKEN_LITERAL_RE.finditer(text)):
            found.append((path, "a long token-like string literal in the code"))
    elif isinstance(value, dict):
        for k, v in value.items():
            sub = f"{path}.{k}" if path else str(k)
            if isinstance(v, str) and v and _secret_name(str(k)):
                found.append((sub, f'a field named "{k}" that looks like a secret'))
            found.extend(scan_secrets(v, sub, code or str(k) in _CODE_KEYS))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found.extend(scan_secrets(v, f"{path}[{i}]"))
    return found


def _clean_url(url: str) -> tuple[str, bool]:
    """Remove userinfo and secret-looking query parameters from a service path / health URL."""
    if not url or ("@" not in url and "?" not in url and "#" not in url):
        return url, False
    try:
        parts = urlsplit(url)
    except ValueError:
        return "", True
    changed = False
    netloc = parts.netloc
    if "@" in netloc:
        netloc, changed = netloc.rsplit("@", 1)[1], True
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not _secret_name(k)]
    changed = changed or len(kept) != len(pairs)
    fragment = parts.fragment
    if fragment and any(_secret_name(k) for k, _ in parse_qsl(fragment, keep_blank_values=True)):
        fragment, changed = "", True
    return (urlunsplit((parts.scheme, netloc, parts.path, urlencode(kept), fragment)) if changed else url), changed


# ---------- export ----------
def export_page(db: Session, dash: models.Dashboard) -> dict:
    """The portable JSON of a page. `warnings` (text only, never a secret value) lists what to
    look at before sharing the file; it is ignored on import."""
    services = {s.id: s for s in db.query(models.Service).all()}
    integs = {i.id: i for i in db.query(models.Integration).all()}
    svc_refs: dict[str, str] = {}
    int_refs: dict[str, str] = {}
    warnings: list[str] = []
    has_layout_sm = hasattr(models.Widget, "layout_sm")

    def sref(service_id: Any) -> Optional[str]:
        if not isinstance(service_id, str) or service_id not in services:
            return None
        return svc_refs.setdefault(service_id, f"s{len(svc_refs) + 1}")

    def iref(integration_id: Any) -> Optional[str]:
        if not isinstance(integration_id, str) or integration_id not in integs:
            return None
        return int_refs.setdefault(integration_id, f"i{len(int_refs) + 1}")

    categories: set[str] = set()
    widgets_out = []
    ordered = sorted(dash.widgets, key=lambda w: (w.y, w.x))
    for n, w in enumerate(ordered, 1):
        if w.type not in catalog():
            warnings.append(f"Widget {n} ({w.type}) was left out: unknown widget type.")
            continue
        cfg = copy.deepcopy(w.config or {})
        if w.type == "calendar" and "integration_ids" not in cfg and isinstance(cfg.get("integration_id"), str):
            cfg["integration_ids"] = [cfg.pop("integration_id")]  # the widget still reads this older key
        for key, kind in id_fields(w.type).items():
            if key not in cfg:
                continue
            to_ref = sref if kind == "service" else iref
            val = cfg[key]
            if isinstance(val, list):
                cfg[key] = [r for r in (to_ref(v) for v in val) if r]
            elif isinstance(val, str) and val:
                ref = to_ref(val)
                if ref:
                    cfg[key] = ref
                else:
                    cfg.pop(key)  # a dangling id: the thing it pointed at is gone
        for key in category_fields(w.type):
            if isinstance(cfg.get(key), str) and cfg[key]:
                categories.add(cfg[key])
        item: dict[str, Any] = {"type": w.type, "x": w.x, "y": w.y, "w": w.w, "h": w.h}
        safe_sm = schemas.sanitize_layout_sm(getattr(w, "layout_sm", None)) if has_layout_sm else None
        if safe_sm:
            item["layout_sm"] = safe_sm
        item["config"] = cfg
        ref = sref(w.service_id)
        if ref:
            item["service_ref"] = ref
        for path, reason in scan_secrets(cfg, code=w.type == "custom"):
            warnings.append(f"Widget {n} ({w.type}), config.{path}: has {reason}. It is exported as is; remove it before sharing.")
        widgets_out.append(item)

    # Widgets that show "all services in a category" carry no service ids: ship the category's services.
    for s in services.values():
        if s.category in categories:
            sref(s.id)

    services_out = []
    for service_id, ref in svc_refs.items():
        s = services[service_id]
        path, path_stripped = _clean_url(s.path or "")  # a path may hold ? and # (Plex, Proxmox ...): only secrets go
        health_url, health_stripped = _clean_url(s.health_url or "")
        host = s.host.rsplit("@", 1)[-1]
        if path_stripped or health_stripped or host != s.host:
            warnings.append(f'Service "{s.name}": credentials or secret-looking URL parameters were removed from its address.')
        services_out.append({
            "ref": ref, "name": s.name, "protocol": s.protocol or "http", "host": host, "port": s.port, "path": path,
            "icon": s.icon or "", "color": s.color or "", "category": s.category or "", "description": s.description or "",
            "health_enabled": bool(s.health_enabled), "health_type": s.health_type or "http", "health_url": health_url,
            "health_interval": s.health_interval or 30,
            "depends_on": [svc_refs[d] for d in (s.depends_on or []) if d in svc_refs],
        })
    # The importer holds services to discovery's rules; one that would be refused is left out (and unlinked).
    bad_refs = set()
    for entry in services_out:
        if entry["icon"] and discovery.clean_icon(entry["icon"]) is None:
            entry["icon"] = ""
        norm = discovery.clean_path(entry["path"])
        if norm is None or discovery._semantic_errors({k: v for k, v in entry.items() if k != "ref"}):
            bad_refs.add(entry["ref"])
            warnings.append(f'Service "{entry["name"]}" was left out: its address is not a plain http(s) host.')
        else:
            entry["path"] = norm
    if bad_refs:
        services_out = [e for e in services_out if e["ref"] not in bad_refs]
        for item in widgets_out:
            if item.get("service_ref") in bad_refs:
                del item["service_ref"]
            for key, kind in id_fields(item["type"]).items():
                if kind == "service" and key in item["config"]:
                    v = item["config"][key]
                    if isinstance(v, list):
                        item["config"][key] = [r for r in v if r not in bad_refs]
                    elif v in bad_refs:
                        del item["config"][key]
        for e in services_out:
            e["depends_on"] = [d for d in e["depends_on"] if d not in bad_refs]
    integrations_out = [{"ref": ref, "type": integs[i].type, "name": integs[i].name} for i, ref in int_refs.items()]

    if any(w["type"] == "custom" for w in widgets_out):
        warnings.append(CUSTOM_CODE_WARNING.replace("Contains", "This page contains"))
    dropped: list[str] = []
    page_settings = {k: v for k, v in (dash.settings or {}).items() if k in PAGE_SETTING_KEYS}
    settings = _clean_settings(page_settings, dropped)  # same whitelist the importer applies
    if any("background image" in d for d in dropped):
        warnings.append("The page's background image was left out (only http(s) addresses are exported).")
    return {
        "format": FORMAT, "version": VERSION, "name": dash.name, "columns": dash.columns, "row_height": dash.row_height,
        "settings": settings, "widgets": widgets_out, "services": services_out,
        "integrations": integrations_out, "warnings": warnings,
        "contents": _contents(widgets_out, services_out, integrations_out),
    }


def _contents(widgets: list[dict], services: list[dict], integrations_out: list[dict]) -> dict:
    """What is in the file, for a "what am I about to share" summary. Counts and yes/no only, never values.
    Like `warnings` it is for the person exporting and ignored on import."""
    types = Counter(w["type"] for w in widgets)

    def has(widget_type: str, key: str) -> bool:
        return any(w["config"].get(key) for w in widgets if w["type"] == widget_type)

    return {
        "widget_count": len(widgets),
        "widget_types": dict(sorted(types.items())),
        "service_count": len(services),
        "integration_count": len(integrations_out),
        "includes": {
            "text_content": has("text", "content") or has("notes", "title") or has("heading", "text") or has("box", "title"),
            "links": has("bookmarks", "links") or has("link", "url"),
            "location": has("weather", "location") or has("clock", "timezone"),
            "custom_code": types.get("custom", 0) > 0,
            "service_addresses": bool(services),
            "phone_layout": any("layout_sm" in w for w in widgets),
        },
    }


# ---------- import: validation of the untrusted file ----------
class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


_REF = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")


def _check_value(value: Any, path: str, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ValueError(f"{path}: nested deeper than {MAX_DEPTH} levels")
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise ValueError(f"{path}: text longer than {MAX_STRING} characters")
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{path}: not a finite number")
    elif isinstance(value, list):
        if len(value) > MAX_ITEMS:
            raise ValueError(f"{path}: more than {MAX_ITEMS} entries")
        for i, v in enumerate(value):
            _check_value(v, f"{path}[{i}]", depth + 1)
    elif isinstance(value, dict):
        if len(value) > MAX_ITEMS:
            raise ValueError(f"{path}: more than {MAX_ITEMS} entries")
        for k, v in value.items():
            if not isinstance(k, str) or len(k) > 200:
                raise ValueError(f"{path}: a key is not text or is too long")
            _check_value(v, f"{path}.{k}", depth + 1)
    else:
        raise ValueError(f"{path}: unsupported value")


URL_KEYS = validation.URL_KEYS
_check_urls = validation.check_urls  # page files: http(s) or relative only (strict)


class PortableService(_Strict):
    ref: str = _REF
    name: str = Field(min_length=1, max_length=200)
    protocol: str = Field("http", max_length=16)
    host: str = Field(min_length=1, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    path: str = Field("", max_length=1000)
    icon: str = Field("", max_length=2000)
    color: str = Field("", max_length=40)
    category: str = Field("", max_length=100)
    description: str = Field("", max_length=2000)
    health_enabled: bool = True
    health_type: Literal["http", "tcp", "ping"] = "http"
    health_url: str = Field("", max_length=1000)
    health_interval: int = Field(30, ge=5, le=86400)
    depends_on: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _as_strict_as_discovery(self) -> "PortableService":
        """The values end up in links and health probes, so they get exactly discovery's checks
        (http/https only, plain host, clean path, http(s) health URL, ...): one per-field 422 for the
        whole import, in preview and in import alike. The path is stored in its normalised form."""
        fields = self.model_dump(exclude={"ref"})
        error = discovery._semantic_errors(fields)
        if error:
            raise ValueError(f"service {self.ref!r}: {error}")
        if self.health_url and validation.url_userinfo(self.health_url):
            raise ValueError(f"service {self.ref!r}: health_url: credentials in a URL are not accepted in a page file")
        self.path = discovery.clean_path(self.path) or ""
        self.host = validation.normalise_host(self.host) or self.host
        self.protocol = self.protocol.strip().lower()
        return self


class PortableIntegration(_Strict):
    """Identity only. A file that carries `config` is refused rather than ignored: secrets must never travel in a page file."""

    ref: str = _REF
    type: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)


def _field_map(widget_type: str) -> dict[str, dict]:
    return {f["key"]: f for f in catalog().get(widget_type, {}).get("fields", [])}


def _short(value: Any) -> str:
    return repr(value if not isinstance(value, str) else value[:40])[:60]


def config_problem(widget_type: str, cfg: dict) -> Optional[str]:
    """"<key>: <why>" for the first config value whose type does not match the catalog field, else None.
    Enforced: text / textarea / url / icon / color / category (a string), number (a number), checkbox (a
    boolean), select (one of the options), service (one ref), services (a list of refs), integration (one
    ref, or a list when `multiple`). null counts as unset. Other field types are not checked here."""
    for key, f in _field_map(widget_type).items():
        if key not in cfg or cfg[key] is None:
            continue
        v, kind = cfg[key], f["type"]
        if kind in ("text", "textarea", "url", "icon", "color", "category") and not isinstance(v, str):
            return f"{key}: must be text"
        if kind == "number" and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return f"{key}: must be a number"
        if kind == "checkbox" and not isinstance(v, bool):
            return f"{key}: must be true or false"
        if kind == "select" and not any(v == o["value"] and type(v) is type(o["value"]) for o in f.get("options", [])):
            return f"{key}: must be one of {[o['value'] for o in f.get('options', [])][:12]}"
        if kind == "service" and not isinstance(v, str):
            return f"{key}: must be a single service ref (text)"
        if kind == "services" or (kind == "integration" and f.get("multiple")):
            if not isinstance(v, list) or not all(isinstance(r, str) for r in v):
                return f"{key}: must be a list of {'service' if kind == 'services' else 'integration'} refs"
        elif kind == "integration" and not isinstance(v, str):
            return f"{key}: must be a single integration ref (text)"
    return None


class PortableWidget(_Strict):
    type: str = Field(min_length=1, max_length=64)
    x: float = Field(0, ge=0, le=100_000, allow_inf_nan=False)
    y: float = Field(0, ge=0, le=100_000, allow_inf_nan=False)
    w: float = Field(gt=0, le=MAX_COLUMNS, allow_inf_nan=False)
    h: float = Field(gt=0, le=1000, allow_inf_nan=False)
    layout_sm: Optional[schemas.PhoneLayout] = None
    config: dict[str, Any] = Field(default_factory=dict)
    service_ref: Optional[str] = None

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in catalog():
            raise ValueError(f"unknown widget type {v!r}")
        return v

    @model_validator(mode="after")
    def _config(self) -> "PortableWidget":
        _check_value(self.config, "config")
        if len(json.dumps(self.config)) > MAX_CONFIG_BYTES:
            raise ValueError(f"config is larger than {MAX_CONFIG_BYTES // 1000} KB")
        _check_urls(self.config, "config", self.type)
        problem = config_problem(self.type, self.config)
        if problem:
            raise ValueError(f"config.{problem}")
        return self


PAGE_SETTING_KEYS = {"gap", "snap", "show_grid", "background_image", "background_dim", "background_blur", "phone_layout", "columns_sm"}


class PortablePage(BaseModel):
    """The document. Unknown top-level keys are ignored (templates add id/title/..., exports add `warnings`)."""

    model_config = ConfigDict(extra="ignore")
    name: str = Field("Imported page", min_length=1, max_length=200)
    columns: int = Field(24, ge=4, le=MAX_COLUMNS)
    row_height: int = Field(40, ge=10, le=400)
    settings: dict[str, Any] = Field(default_factory=dict)
    widgets: list[PortableWidget] = Field(default_factory=list, max_length=MAX_WIDGETS)
    services: list[PortableService] = Field(default_factory=list, max_length=MAX_SERVICES)
    integrations: list[PortableIntegration] = Field(default_factory=list, max_length=MAX_INTEGRATIONS)

    @field_validator("settings")
    @classmethod
    def _settings(cls, v: dict) -> dict:
        _check_value(v, "settings")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> "PortablePage":
        svc_refs = [s.ref for s in self.services]
        int_refs = [i.ref for i in self.integrations]
        if len(set(svc_refs)) != len(svc_refs):
            raise ValueError("services: two entries share a ref")
        if len(set(int_refs)) != len(int_refs):
            raise ValueError("integrations: two entries share a ref")
        unknown_types = sorted({i.type for i in self.integrations} - set(integrations.SPECS))
        if unknown_types:
            raise ValueError(f"integrations: unknown type(s) {unknown_types}")
        known_svc, known_int = set(svc_refs), set(int_refs)
        for s in self.services:
            gone = [d for d in s.depends_on if d not in known_svc]
            if gone:
                raise ValueError(f"services.{s.ref}.depends_on: unknown ref(s) {[_short(g) for g in gone[:5]]}")
        for i, w in enumerate(self.widgets):
            where = f"widgets.{i} ({w.type})"
            if w.x + w.w > self.columns + 1e-6:
                raise ValueError(f"{where}: extends past the page's {self.columns} columns (x {w.x} + w {w.w})")
            if w.service_ref is not None and w.service_ref not in known_svc:
                raise ValueError(f"{where}.service_ref: unknown service ref {_short(w.service_ref)}")
            for key, kind in id_fields(w.type).items():
                known = known_svc if kind == "service" else known_int
                val = w.config.get(key)
                refs = val if isinstance(val, list) else [val] if isinstance(val, str) and val else []
                for r in refs:
                    if not isinstance(r, str) or r not in known:
                        raise ValueError(
                            f"{where}.config.{key}: {_short(r)} is not a {kind} ref defined in this file "
                            f"(page files refer to {kind}s by ref, never by database id)"
                        )
        return self


def _errors(e: ValidationError) -> list[dict]:
    """FastAPI-style 422 detail: a list of {loc, msg}; never echoes the input."""
    out = []
    for err in e.errors(include_url=False, include_context=False, include_input=False):
        msg = str(err["msg"])
        out.append({"loc": [str(p) for p in err["loc"]], "msg": msg.removeprefix("Value error, ")})
    return out


def parse_page(doc: Any) -> PortablePage:
    """Validate an untrusted page document; HTTP 422 with a clear message when it is not acceptable."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise HTTPException(422, f'Not a Start Page page file: "format" must be "{FORMAT}".')
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise HTTPException(422, '"version" must be a whole number, 1 or higher.')
    if version > VERSION:
        raise HTTPException(422, f"This page file is version {version}; this Start Page reads version {VERSION}. Update Start Page to import it.")
    try:
        return PortablePage.model_validate(doc)
    except ValidationError as e:
        raise HTTPException(422, _errors(e)) from None
    except RecursionError:
        raise HTTPException(422, "The page file is nested too deeply") from None


def validate_model(model: type[BaseModel], data: Any) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise HTTPException(422, _errors(e)) from None
    except RecursionError:
        raise HTTPException(422, "The request body is nested too deeply") from None


class ApplyOptions(BaseModel):
    """What happens to services and integrations on import (shared by import, preview and template apply)."""

    name: Optional[str] = Field(None, max_length=200)
    service_mode: Literal["match", "create", "skip"] = "match"
    integration_map: Optional[dict[str, Optional[str]]] = Field(None, max_length=100)


class ImportRequest(ApplyOptions):
    page: dict[str, Any]


async def read_json_body(request: Request) -> Any:
    """The request body as JSON, refusing anything over MAX_BODY_BYTES before it is all in memory."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise HTTPException(413, f"Request is larger than {MAX_BODY_BYTES // (1024 * 1024)} MB")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise HTTPException(413, f"Request is larger than {MAX_BODY_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(422, "The request body is not valid JSON (or is nested too deeply)") from None


# ---------- import: plan, summary, commit ----------
@dataclass
class Plan:
    name: str
    columns: int
    row_height: int
    settings: dict
    widgets: list[dict] = field(default_factory=list)  # ready to insert: id, type, x, y, w, h, config, service_id, layout_sm
    services: list[dict] = field(default_factory=list)  # {ref, action, id, data, matched_by}
    integrations: list[dict] = field(default_factory=list)  # {ref, type, name, id, via, widgets: [index]}
    warnings: list[str] = field(default_factory=list)
    custom_widgets: int = 0


def _clean_settings(settings: dict, warnings: list[str]) -> dict:
    out: dict[str, Any] = {}
    for key, v in settings.items():
        if key not in PAGE_SETTING_KEYS:
            warnings.append(f'Page setting "{key}" was ignored.')
        elif key in ("gap", "background_dim", "background_blur"):
            if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100:
                out[key] = v
        elif key == "show_grid":
            out[key] = bool(v)
        elif key == "columns_sm":
            if type(v) is int and 1 <= v <= schemas.PHONE_COLS_MAX:
                out[key] = v
            else:
                warnings.append(f'Page setting "columns_sm" was ignored (a whole number from 1 to {schemas.PHONE_COLS_MAX}).')
        elif key == "phone_layout":
            if v in schemas.PHONE_LAYOUTS:
                out[key] = v
            else:
                warnings.append('Page setting "phone_layout" was ignored (auto or custom).')
        elif key == "snap":
            if isinstance(v, str) and len(v) <= 20:
                out[key] = v
        elif key == "background_image":
            if isinstance(v, str) and (v == "" or (len(v) <= 2000 and re.match(r"^(https?://|/)", v))):
                out[key] = v
            else:
                warnings.append("The page's background image was ignored (only http(s) addresses are imported).")
    return out


def _find_match(existing: list, s: PortableService) -> tuple[Optional[Any], str]:
    """An existing service for a file service: the same host and port ("address"), else the same name on
    the same host (another port). A name alone never matches when the file gives a host (it always does
    for a valid file): a file's "nas" at another address must not link to the user's "NAS"."""
    host = s.host.lower()
    same_addr = [e for e in existing if (e.host or "").lower() == host and e.port == s.port]
    if same_addr:
        return next((e for e in same_addr if e.protocol == s.protocol), same_addr[0]), "address"
    name = s.name.strip().casefold()
    by_name = next((e for e in existing if (e.name or "").strip().casefold() == name
                    and (not host or (e.host or "").lower() == host)), None)
    return (by_name, "name") if by_name else (None, "")


def plan_import(db: Session, page: PortablePage, opts: ApplyOptions) -> Plan:
    """Decide everything an import will do, without writing."""
    warnings: list[str] = []
    name = (opts.name or "").strip() or page.name.strip() or "Imported page"
    plan = Plan(name=name[:200], columns=page.columns, row_height=page.row_height,
                settings=_clean_settings(page.settings, warnings))

    # services
    existing_services = db.query(models.Service).all()
    svc_id: dict[str, Optional[str]] = {}
    for s in page.services:
        if opts.service_mode == "skip":
            plan.services.append({"ref": s.ref, "name": s.name, "action": "skip", "id": None, "data": None, "matched_by": ""})
            svc_id[s.ref] = None
            continue
        match, by = _find_match(existing_services, s) if opts.service_mode == "match" else (None, "")
        if match:
            plan.services.append({"ref": s.ref, "name": s.name, "action": "match", "id": match.id, "data": None, "matched_by": by})
            svc_id[s.ref] = match.id
        else:
            new_id = models.new_id()
            plan.services.append({"ref": s.ref, "name": s.name, "action": "create", "id": new_id, "data": s, "matched_by": ""})
            svc_id[s.ref] = new_id
            if opts.service_mode == "match":  # a later service at the same address in this file matches this one
                existing_services.append(SimpleNamespace(id=new_id, host=s.host, port=s.port, protocol=s.protocol, name=s.name))
    for entry in plan.services:
        if entry["action"] == "create":
            data = entry["data"]
            entry["data"] = {
                **data.model_dump(exclude={"ref", "depends_on"}),
                "depends_on": [svc_id[d] for d in data.depends_on if svc_id.get(d)],
            }

    # integrations
    imap = opts.integration_map or {}
    stray = sorted(set(imap) - {i.ref for i in page.integrations})
    if stray:
        raise HTTPException(422, f"integration_map names ref(s) that are not in the page: {stray}")
    existing_ints = db.query(models.Integration).order_by(models.Integration.name).all()
    by_id = {i.id: i for i in existing_ints}
    int_id: dict[str, Optional[str]] = {}
    for i in page.integrations:
        entry = {"ref": i.ref, "type": i.type, "name": i.name, "id": None, "via": "none", "widgets": []}
        if i.ref in imap:
            chosen = imap[i.ref]
            if chosen:
                row = by_id.get(chosen)
                if row is None:
                    raise HTTPException(422, f"integration_map[{i.ref!r}]: no integration with id {chosen!r}")
                if row.type != i.type:
                    raise HTTPException(422, f"integration_map[{i.ref!r}]: {row.name!r} is a {row.type} integration, the page needs {i.type}")
                entry.update(id=row.id, via="map")
        else:
            same = [e for e in existing_ints if e.type == i.type]
            if len(same) == 1:
                entry.update(id=same[0].id, via="auto")
        int_id[i.ref] = entry["id"]
        plan.integrations.append(entry)
    entry_of = {e["ref"]: e for e in plan.integrations}

    # widgets
    has_layout_sm = hasattr(models.Widget, "layout_sm")
    phone_cols = schemas.phone_columns(plan.settings)
    for n, w in enumerate(page.widgets):
        cfg = copy.deepcopy(w.config)
        known_keys = _field_map(w.type)
        unknown = sorted(k for k in cfg if k not in known_keys)
        for k in unknown:  # not a setting of this widget type: dropped (same in preview and import)
            cfg.pop(k)
        if unknown:
            warnings.append(f"Widget {n + 1} ({w.type}): the unknown setting(s) {', '.join(repr(k[:40]) for k in unknown[:5])} were ignored.")
        for key, kind in id_fields(w.type).items():
            if key not in cfg:
                continue
            if w.type == "custom":
                # Code from a file never gets integration or service data until the user links it in the
                # widget's settings: nothing is auto-matched or mapped for it.
                cfg.pop(key)
                continue
            table = svc_id if kind == "service" else int_id
            val = cfg[key]
            if isinstance(val, list):
                cfg[key] = [table[r] for r in val if table.get(r)]
                if kind == "integration":
                    for r in val:
                        entry_of[r]["widgets"].append(n)
            elif isinstance(val, str) and val:
                if kind == "integration":
                    entry_of[val]["widgets"].append(n)
                if table.get(val):
                    cfg[key] = table[val]
                else:
                    cfg.pop(key)
        row = {"id": models.new_id(), "type": w.type, "x": w.x, "y": w.y, "w": w.w, "h": w.h, "config": cfg,
               "service_id": svc_id.get(w.service_ref) if w.service_ref and w.type != "custom" else None}
        if has_layout_sm and w.layout_sm:
            if schemas.fit_error(w.layout_sm.x, w.layout_sm.w, phone_cols):
                warnings.append(f"Widget {n + 1} ({w.type}): its phone position does not fit the page's {phone_cols} phone columns and was dropped.")
            else:
                row["layout_sm"] = w.layout_sm.model_dump()
        plan.widgets.append(row)
        for path, reason in scan_secrets(w.config, code=w.type == "custom"):
            warnings.append(f"Widget {n + 1} ({w.type}), config.{path}: has {reason}.")
        if w.type == "custom":
            plan.custom_widgets += 1
    if plan.custom_widgets:
        warnings.insert(0, f"{CUSTOM_CODE_WARNING} ({plan.custom_widgets} custom widget{'s' if plan.custom_widgets != 1 else ''}; "
                           "imported without integration or service links: link them in the widget settings if you trust the code)")
    plan.warnings = warnings
    return plan


def summarize(plan: Plan, dashboard: Optional[dict] = None) -> dict:
    """The response of import / preview / apply. Without `dashboard` (a preview) nothing exists yet:
    created services have no id and unresolved integrations list no widget ids."""
    written = dashboard is not None
    widget_ids = [w["id"] for w in plan.widgets]
    return {
        "dashboard": dashboard or {"id": None, "name": plan.name, "columns": plan.columns, "row_height": plan.row_height,
                                   "settings": plan.settings, "widgets": []},
        "widget_count": len(plan.widgets),
        "custom_widgets": plan.custom_widgets,
        "created_services": [
            {"ref": s["ref"], "id": s["id"] if written else None, "name": s["name"]} for s in plan.services if s["action"] == "create"
        ],
        "matched_services": [
            {"ref": s["ref"], "id": s["id"], "name": s["name"], "matched_by": s["matched_by"]} for s in plan.services if s["action"] == "match"
        ],
        "skipped_services": [{"ref": s["ref"], "name": s["name"]} for s in plan.services if s["action"] == "skip"],
        "matched_integrations": [
            {"ref": i["ref"], "id": i["id"], "type": i["type"], "name": i["name"], "via": i["via"]} for i in plan.integrations if i["id"]
        ],
        "unresolved_integrations": [
            {"ref": i["ref"], "type": i["type"], "name": i["name"],
             "widget_ids": [widget_ids[n] for n in i["widgets"]] if written else [], "widget_count": len(set(i["widgets"]))}
            for i in plan.integrations if not i["id"]
        ],
        "warnings": plan.warnings,
    }


def commit_plan(db: Session, plan: Plan) -> dict:
    """Write the plan: a new page with its widgets and any new services, in one transaction. The response
    (the new page as the API shows it) is built before the commit: if that fails, nothing is written."""
    try:
        dash = models.Dashboard(
            name=plan.name, position=db.query(models.Dashboard).count(), columns=plan.columns,
            row_height=plan.row_height, settings=plan.settings,
        )
        db.add(dash)
        db.flush()
        for s in plan.services:
            if s["action"] == "create":
                data = dict(s["data"])
                db.add(models.Service(id=s["id"], **data))
        db.flush()
        for w in plan.widgets:
            db.add(models.Widget(dashboard_id=dash.id, **w))
        db.flush()
        db.refresh(dash)
        detail = schemas.DashboardDetail.model_validate(dash).model_dump()
        db.commit()
        return detail
    except BaseException:
        db.rollback()
        raise


def run_import(db: Session, page: PortablePage, opts: ApplyOptions, preview: bool = False) -> dict:
    plan = plan_import(db, page, opts)
    if preview:
        return summarize(plan)
    return summarize(plan, commit_plan(db, plan))
