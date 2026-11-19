"""Service discovery: turn Docker containers into service candidates, and import the chosen ones.

Nothing here writes to the database except `import_items`, which only runs what the user sent
back. Container labels are untrusted input: every value is validated and length-capped here, and a
malformed one becomes a line in the candidate's `reasons` (or a `skipped` row), never an error.
Each candidate is derived from its own container's data only.

Rules, in priority order (documented for users in the README):
  1. Labels: `startpage.*`, then the Homepage labels `homepage.*` as a fallback.
  2. Traefik router rules: Host(`a.example`) [&& PathPrefix(`/x`)]; the proxy serves 80/443.
  3. Published TCP ports: image map -> well-known web port -> lowest published port (a guess).
"""
import re
from typing import Any, Optional

from pydantic import ValidationError

from . import models, schemas

from .validation import (  # noqa: F401  (shared rules; re-exported so callers keep using discovery.*)
    CTRL as _CTRL, DEFAULT_PORT, HEALTH_TYPES, MAX_CATEGORY, MAX_DESC, MAX_ICON, MAX_NAME, MAX_PATH, MAX_URL,
    PROTOCOLS, clean_icon, clean_path, clean_port, clean_text, parse_url, semantic_errors as _semantic_errors,
    url_host, valid_host,
)

MAX_ITEMS = 200
_FALSE = {"false", "0", "no", "off"}

# ---------------------------------------------------------------- image knowledge
# image name (last path segment, or `namespace/name`) -> (container web port, protocol)
IMAGE_MAP: dict[str, tuple[int, str]] = {
    "plex": (32400, "http"), "pms-docker": (32400, "http"), "jellyfin": (8096, "http"), "emby": (8096, "http"),
    "embyserver": (8096, "http"), "sonarr": (8989, "http"), "radarr": (7878, "http"), "lidarr": (8686, "http"),
    "readarr": (8787, "http"), "bazarr": (6767, "http"), "prowlarr": (9696, "http"), "overseerr": (5055, "http"),
    "jellyseerr": (5055, "http"), "tautulli": (8181, "http"), "qbittorrent": (8080, "http"),
    "qbittorrent-nox": (8080, "http"), "transmission": (9091, "http"), "deluge": (8112, "http"),
    "sabnzbd": (8080, "http"), "nzbget": (6789, "http"), "portainer": (9443, "https"),
    "portainer-ce": (9443, "https"), "pihole": (80, "http"), "adguardhome": (3000, "http"),
    "home-assistant": (8123, "http"), "homeassistant": (8123, "http"), "grafana": (3000, "http"),
    "prometheus": (9090, "http"), "uptime-kuma": (3001, "http"), "nextcloud": (80, "http"),
    "vaultwarden": (80, "http"), "vaultwarden/server": (80, "http"), "gitea": (3000, "http"),
    "immich-server": (2283, "http"), "paperless-ngx": (8000, "http"), "filebrowser": (80, "http"),
    "heimdall": (80, "http"), "homer": (8080, "http"), "dozzle": (8080, "http"),
    "nginx-proxy-manager": (81, "http"), "syncthing": (8384, "http"), "photoprism": (2342, "http"),
    "audiobookshelf": (80, "http"), "calibre-web": (8083, "http"), "mealie": (9000, "http"), "n8n": (5678, "http"),
    "node-red": (1880, "http"), "duplicati": (8200, "http"), "bookstack": (80, "http"), "freshrss": (80, "http"),
    "miniflux": (8080, "http"), "changedetection.io": (5000, "http"), "glances": (61208, "http"),
    "netdata": (19999, "http"), "esphome": (6052, "http"), "zigbee2mqtt": (8080, "http"),
    "searxng": (8080, "http"), "open-webui": (8080, "http"), "whoami": (80, "http"),
}
WELL_KNOWN_PORTS = (80, 443, 8080, 8443, 3000, 8000, 8008, 8081, 8888, 9000, 5000, 3001, 8123, 8096, 9090)
HTTPS_PORTS = (443, 8443, 9443)

# Not a web UI: databases, caches, brokers, plumbing. Shown under `skipped` unless explicitly labelled.
DENYLIST = {
    "postgres", "postgresql", "mariadb", "mysql", "mongo", "mongodb", "redis", "valkey", "keydb", "memcached",
    "influxdb", "elasticsearch", "opensearch", "rabbitmq", "kafka", "zookeeper", "cassandra", "clickhouse",
    "watchtower", "autoheal", "diun", "ouroboros", "cloudflared", "tailscale", "docker-socket-proxy",
    "socket-proxy", "dockerproxy", "start-page", "startpage",
}


# ---------------------------------------------------------------- helpers
def pretty(slug: str) -> str:
    """`uptime-kuma` -> `Uptime Kuma`; words that already have capitals are kept."""
    words = re.sub(r"[-_.]+", " ", slug).split()
    return " ".join(w if any(ch.isupper() for ch in w) else w[:1].upper() + w[1:] for w in words)


def build_url(protocol: str, host: str, port: Optional[int], path: str) -> str:
    """Same shape as models.Service.url."""
    shown = "" if port is None or port == DEFAULT_PORT.get(protocol) else f":{port}"
    return f"{protocol}://{url_host(host)}{shown}{path}"


# ---------------------------------------------------------------- images and traefik
def image_keys(image: str) -> list[str]:
    """Lookup keys for an image reference: `ns/name` and `name`, lowercase, tag and digest removed."""
    ref = image.split("@")[0].lower()
    parts = [p for p in ref.split("/") if p]
    if not parts:
        return []
    name = parts[-1].split(":")[0]
    return ([f"{parts[-2]}/{name}"] if len(parts) > 1 else []) + [name]


def image_lookup(image: str) -> tuple[Optional[str], Optional[tuple[int, str]]]:
    keys = image_keys(image)
    for key in keys:
        if key in IMAGE_MAP:
            return key, IMAGE_MAP[key]
    return None, None


_ROUTER = re.compile(r"^traefik\.http\.routers\.([^.]+)\.(.+)$")
MAX_LABELS = 500  # labels read per container
MAX_ROUTERS = 20  # Traefik routers examined per container
_HOST = re.compile(r"(?<![A-Za-z!])Host\(([^)]{0,500})\)")  # bounded: no quadratic scan on a rule full of `Host(`
_TICK = re.compile(r"`([^`]*)`")
_PREFIX = re.compile(r"(?<![A-Za-z])PathPrefix\(\s{0,20}`([^`]{0,500})`")


def parse_rule(rule: str) -> Optional[tuple[str, str]]:
    """(first host, first PathPrefix or "") from a Traefik rule; None without a usable Host(...)."""
    rule = rule[:2000]
    host = next((t.strip() for m in _HOST.finditer(rule) for t in _TICK.findall(m.group(1)) if valid_host(t.strip())), None)
    if host is None:
        return None
    path = ""
    for m in _PREFIX.finditer(rule):
        cleaned = clean_path(m.group(1))
        if cleaned:
            path = cleaned
            break
    return host.lower(), path


def traefik_route(labels: dict, reasons: list) -> Optional[dict]:
    """The first (by router name) router with a Host rule: {host, https, path, router}."""
    if labels.get("traefik.enable", "").strip().lower() in _FALSE:
        return None
    routers: dict[str, dict[str, str]] = {}
    for n, (key, value) in enumerate(labels.items()):
        if n >= MAX_LABELS:
            reasons.append(f"only the first {MAX_LABELS} labels were read")
            break
        if len(key) > 300 or not key.startswith("traefik.http.routers."):
            continue
        m = _ROUTER.match(key)
        if m and (m.group(1) in routers or len(routers) < MAX_ROUTERS):
            routers.setdefault(m.group(1), {})[m.group(2)] = value
        elif m and not any("routers were examined" in r for r in reasons):
            reasons.append(f"only the first {MAX_ROUTERS} Traefik routers were examined")
    for name in sorted(routers):
        props = routers[name]
        if "rule" not in props:
            continue
        parsed = parse_rule(props["rule"])
        if parsed is None:
            reasons.append(f"traefik router {name[:40]!r} has no usable Host rule")
            continue
        entry = props.get("entrypoints", "").lower()
        secure = (
            props.get("tls", "").strip().lower() == "true"
            or (any(k.startswith("tls.") for k in props) and props.get("tls", "true").strip().lower() != "false")
            or bool(re.search(r"websecure|https|443", entry))
        )
        return {"host": parsed[0], "path": parsed[1], "https": secure, "router": name}
    return None


# ---------------------------------------------------------------- ports
def pick_port(c: dict) -> Optional[dict]:
    """The best published TCP port: {public, private, protocol, how, known, loopback}, or None."""
    key, mapped = image_lookup(c["image"])
    if c.get("network_mode") == "host":  # nothing is "published": the app listens on the host itself
        if mapped:
            return {"public": mapped[0], "private": mapped[0], "protocol": mapped[1], "how": f"host network, image map: {key}", "known": True, "loopback": False}
        return None
    seen: dict[tuple[int, int], bool] = {}
    for p in c.get("ports") or []:
        pub, priv = p.get("public"), p.get("private")
        if p.get("type", "tcp") != "tcp" or clean_port(pub) is None or clean_port(priv) is None:
            continue
        loop = str(p.get("ip", "")) in ("127.0.0.1", "::1")
        seen[(pub, priv)] = seen.get((pub, priv), True) and loop  # loopback only if every binding is
    if not seen:
        return None
    ordered = sorted(seen, key=lambda t: (seen[t], t[0]))  # reachable bindings first
    choice, how, known = None, "", True
    if mapped:
        choice = next((t for t in ordered if t[1] == mapped[0]), None)
        how = f"image map: {key}"
    if choice is None:
        for wk in WELL_KNOWN_PORTS:
            choice = next((t for t in ordered if t[1] == wk), None)
            if choice:
                how = "well-known web port"
                break
    if choice is None:
        choice, how, known = ordered[0], "first published port (a guess)", False
    protocol = mapped[1] if mapped and choice[1] == mapped[0] else ("https" if choice[1] in HTTPS_PORTS else "http")
    return {"public": choice[0], "private": choice[1], "protocol": protocol, "how": how, "known": known, "loopback": seen[choice]}


# ---------------------------------------------------------------- derivation
def _pick(labels: dict, *keys: str) -> tuple[str, str]:
    for k in keys:
        v = labels.get(k)
        if isinstance(v, str) and v.strip():
            return k, v
    return "", ""


def derive(c: dict, fallback_host: str, host_note: str, icon_names: Optional[set]) -> tuple[Optional[dict], str, set]:
    """One container -> (candidate or None, skip reason, evidence).

    `evidence` is the set of service fields that come from real evidence (labels, Traefik) rather
    than from a guess; only those may be proposed as changes to an existing service."""
    labels: dict = c.get("labels") or {}
    reasons: list[str] = []
    evidence: set[str] = set()
    explicit = any(k.startswith(("startpage.", "homepage.")) for k in labels)

    if labels.get("startpage.enable", "").strip().lower() in _FALSE:
        return None, "excluded by startpage.enable=false", evidence
    img_key = image_keys(c["image"])[-1] if image_keys(c["image"]) else ""
    if not explicit and (img_key in DENYLIST or c["name"].lower() in ("startpage", "start-page")):
        return None, f"not a web UI ({img_key or c['name']}); add a startpage.* label to include it", evidence

    # --- connection
    proto: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    path = ""
    confidence = ""
    part_labels = {k: labels.get(f"startpage.{k}") for k in ("protocol", "host", "port", "path")}
    url_key, url_raw = _pick(labels, "startpage.url", "homepage.href")
    if url_key:
        parsed, why = parse_url(url_raw)
        if parsed:
            proto, host, port, path = parsed["protocol"], parsed["host"], parsed["port"], parsed["path"]
            confidence = "label"
            evidence |= {"protocol", "host", "port", "path"}
            reasons.append(f"URL from the {url_key} label")
            if any(v for v in part_labels.values()):
                reasons.append(f"{url_key} wins over the protocol/host/port/path labels")
        else:
            reasons.append(f"{url_key} label ignored: {why}")

    traefik = None
    picked = None
    if not confidence:
        l_proto = l_host = l_port = None
        l_path = ""
        raw = part_labels["protocol"]
        if raw is not None and raw.strip():
            if raw.strip().lower() in PROTOCOLS:
                l_proto = raw.strip().lower()
            else:
                reasons.append("startpage.protocol label ignored: must be http or https")
        raw = part_labels["host"]
        if raw is not None and raw.strip():
            if valid_host(raw.strip()):
                l_host = raw.strip()
            else:
                reasons.append("startpage.host label ignored: not a plain hostname or IPv4 address")
        raw = part_labels["port"]
        if raw is not None and raw.strip():
            l_port = clean_port(raw)
            if l_port is None:
                reasons.append("startpage.port label ignored: must be 1-65535")
        raw = part_labels["path"]
        if raw is not None and raw.strip():
            cleaned = clean_path(raw)
            if cleaned is None:
                reasons.append("startpage.path label ignored: invalid path")
            else:
                l_path = cleaned
        for name, val in (("protocol", l_proto), ("host", l_host), ("port", l_port)):
            if val is not None:
                evidence.add(name)
        if l_path:
            evidence.add("path")

        traefik = traefik_route(labels, reasons) if l_host is None else None
        if traefik:
            host = traefik["host"]
            proto = l_proto or ("https" if traefik["https"] else "http")
            path = l_path or traefik["path"]
            port = l_port
            confidence = "label" if l_port is not None else "traefik"
            evidence |= {"protocol", "host", "port"} | ({"path"} if path else set())
            reasons.append(f"Traefik router {traefik['router'][:40]!r}: Host rule, {'https' if proto == 'https' else 'http'}, port served by the proxy")
        else:
            picked = pick_port(c) if l_port is None else None
            if l_port is None and picked is None and l_host is None:
                note = "host network mode and the image has no known web port" if c.get("network_mode") == "host" else "no published TCP port"
                return None, f"{note}, no URL labels and no Traefik Host rule", evidence
            host = l_host or fallback_host
            port = l_port if l_port is not None else (picked["public"] if picked else None)
            proto = l_proto or (picked["protocol"] if picked else "http")
            path = l_path
            if l_host is None:
                reasons.append(f"host {fallback_host} {host_note}")
            if l_port is not None:
                confidence = "label"
                reasons.append("port from the startpage.port label")
            elif picked:
                confidence = "port" if picked["known"] else "guess"
                reasons.append(f"published port {picked['public']}->{picked['private']} ({picked['how']})")
                if picked["loopback"]:
                    reasons.append("published on 127.0.0.1 only: other devices may not reach it")
            else:
                confidence = "label"
            if l_host is not None and l_port is None and picked is None:
                reasons.append("no port given: the protocol's default port is used")
    assert proto and host
    if confidence == "guess" and explicit:
        confidence = "port"
        reasons.append("container carries startpage/homepage labels")

    # --- name
    compose_service = labels.get("com.docker.compose.service", "").strip()
    name_key, name_raw = _pick(labels, "startpage.name", "homepage.name")
    name = ""
    if name_key:
        name, cut = clean_text(name_raw, MAX_NAME)
        if cut:
            reasons.append(f"{name_key} label truncated to {MAX_NAME} characters")
        if name:
            evidence.add("name")
            reasons.append(f"name from the {name_key} label")
    if not name:
        name = pretty(compose_service or c["name"])[:MAX_NAME] or "Service"
        reasons.append("name from the compose service" if compose_service else "name from the container name")

    # --- category
    category = ""
    cat_key, cat_raw = _pick(labels, "startpage.category", "startpage.group", "homepage.group")
    if cat_key:
        category, cut = clean_text(cat_raw, MAX_CATEGORY)
        if cut:
            reasons.append(f"{cat_key} label truncated to {MAX_CATEGORY} characters")
        if category:
            evidence.add("category")
    if not category and labels.get("com.docker.compose.project", "").strip():
        category, _ = clean_text(pretty(labels["com.docker.compose.project"]), MAX_CATEGORY)
        if category:
            reasons.append("category from the compose project")

    # --- description
    description = ""
    desc_key, desc_raw = _pick(labels, "startpage.description", "homepage.description")
    if desc_key:
        description, cut = clean_text(desc_raw, MAX_DESC)
        if cut:
            reasons.append(f"{desc_key} label truncated to {MAX_DESC} characters")
        if description:
            evidence.add("description")

    # --- icon
    icon = ""
    icon_key, icon_raw = _pick(labels, "startpage.icon", "homepage.icon")
    if icon_key:
        cleaned = clean_icon(icon_raw)
        if cleaned:
            icon = cleaned
            evidence.add("icon")
        else:
            reasons.append(f"{icon_key} label ignored: not an icon name, emoji, URL or path")
    if not icon:
        slugs = [s for s in (image_keys(c["image"])[-1:] + [compose_service.lower(), c["name"].lower()]) if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,60}", s or "") and s != "sha256"]
        if icon_names:
            hit = next((s for s in slugs if s in icon_names), None)
            if hit:
                icon = hit
                reasons.append(f"icon {hit!r} found in dashboard-icons")
        elif slugs:
            icon = slugs[0]
            reasons.append(f"icon {icon!r} proposed from the image name, not verified (dashboard-icons list not loaded)")

    # --- health
    health = {"enabled": True, "type": "http"}
    h_raw = labels.get("startpage.health", "").strip().lower()
    if h_raw:
        if h_raw == "off":
            health = {"enabled": False, "type": "http"}
            evidence.add("health")
        elif h_raw in HEALTH_TYPES:
            health = {"enabled": True, "type": h_raw}
            evidence.add("health")
        else:
            reasons.append("startpage.health label ignored: must be http, tcp, ping or off")

    candidate = {
        "key": f"{c['integration_id']}:{c['id']}",
        "source": {
            "integration_id": c["integration_id"], "integration_name": c["integration_name"],
            "container_id": c["id"], "container_name": c["name"], "image": c["image"], "state": c["state"],
        },
        "name": name, "protocol": proto, "host": host, "port": port, "path": path,
        "url": build_url(proto, host, port, path), "icon": icon, "category": category, "description": description,
        "health": health, "confidence": confidence, "reasons": reasons,
        "existing_service_id": None, "changes": None,
    }
    return candidate, "", evidence


# ---------------------------------------------------------------- matching against the registry
def _eff(protocol: Optional[str], port: Optional[int]) -> Optional[int]:
    return port if port is not None else DEFAULT_PORT.get(protocol or "")


def match_existing(cand: dict, evidence: set, services: list) -> None:
    """Set `existing_service_id` and `changes` on a candidate.

    Matched by host + effective port (a missing port means 80/443 by protocol), else by name
    (case-insensitive) against the container name or the candidate's name. `changes` holds only
    fields with real evidence (labels, Traefik) that differ: guesses never overwrite anything."""
    addr = (cand["host"].lower(), _eff(cand["protocol"], cand["port"]))
    svc = next((s for s in services if (s.host or "").lower() == addr[0] and _eff(s.protocol, s.port) == addr[1]), None)
    how = "host and port"
    if svc is None:
        names = {cand["source"]["container_name"].lower(), cand["name"].lower()}
        svc = next((s for s in services if (s.name or "").lower() in names), None)
        how = "name"
    if svc is None:
        return
    cand["existing_service_id"] = svc.id
    cand["reasons"].append(f"matches your service {svc.name!r} by {how}")
    current = {
        "name": svc.name, "protocol": svc.protocol, "host": svc.host, "port": svc.port, "path": svc.path or "",
        "icon": svc.icon or "", "category": svc.category or "", "description": svc.description or "",
        "health_enabled": bool(svc.health_enabled), "health_type": svc.health_type or "http",
    }
    proposed = {
        "name": cand["name"], "protocol": cand["protocol"], "host": cand["host"], "port": cand["port"],
        "path": cand["path"], "icon": cand["icon"], "category": cand["category"], "description": cand["description"],
        "health_enabled": cand["health"]["enabled"], "health_type": cand["health"]["type"],
    }
    changes: dict[str, list] = {}
    for f in ("name", "protocol", "host", "port", "path", "icon", "category", "description"):
        if f not in evidence or not (proposed[f] or f in ("port",)):
            continue  # no evidence, or a label that cleaned down to nothing: never blank a field
        cur, new = current[f], proposed[f]
        if f == "host":
            same = (cur or "").lower() == new.lower()
        elif f == "port":
            same = _eff(current["protocol"], cur) == _eff(proposed["protocol"], new)
        else:
            same = cur == new
        if not same:
            changes[f] = [cur, new]
    if "health" in evidence:
        for f in ("health_enabled", "health_type"):
            if f == "health_type" and not proposed["health_enabled"]:
                continue
            if current[f] != proposed[f]:
                changes[f] = [current[f], proposed[f]]
    cand["changes"] = changes or None


# ---------------------------------------------------------------- import
SERVICE_FIELDS = {
    "name", "protocol", "host", "port", "path", "icon", "color", "category", "description",
    "health_enabled", "health_type", "health_url", "health_interval", "depends_on",
}


def _validation_message(exc: ValidationError) -> str:
    e = exc.errors()[0]
    loc = ".".join(str(p) for p in e.get("loc", ())) or "fields"
    return f"{loc}: {e.get('msg', 'invalid')}"


def import_items(db, items: list[dict], to_out) -> dict:
    """Create/update services from the user's chosen (and possibly edited) fields.

    Per-item: an item that fails validation is reported in `errors` and the rest still go
    through. The valid ones are applied in ONE transaction: a database failure rolls all of them back."""
    created: list[dict] = []
    updated: list[dict] = []
    errors: list[dict] = []
    todo: list[tuple[str, str, models.Service]] = []  # (key, action, row)
    seen_updates: set[str] = set()
    for item in items:
        key, action, fields = item["key"], item["action"], item["fields"]
        unknown = sorted(set(fields) - SERVICE_FIELDS)
        if unknown:
            errors.append({"key": key, "error": f"unknown field(s): {', '.join(unknown)}"})
            continue
        try:
            if action == "create":
                data = schemas.ServiceCreate(**fields).model_dump(exclude_none=True)
                bad = _semantic_errors({**data})
                if bad:
                    errors.append({"key": key, "error": bad})
                    continue
                kwargs = dict(data)
                kwargs["metadata_"] = kwargs.pop("metadata", {})
                kwargs.pop("id", None)
                todo.append((key, action, models.Service(**kwargs)))
            else:
                sid = item.get("service_id")
                svc = db.get(models.Service, sid) if sid else None
                if svc is None:
                    errors.append({"key": key, "error": "service_id is missing or does not exist"})
                    continue
                if sid in seen_updates:
                    errors.append({"key": key, "error": "the same service is updated twice in one import"})
                    continue
                data = schemas.patch_data(schemas.ServiceUpdate(**fields), nullable=("port",))
                bad = _semantic_errors(data)
                if bad:
                    errors.append({"key": key, "error": bad})
                    continue
                seen_updates.add(sid)
                todo.append((key, action, (svc, data)))  # type: ignore[arg-type]
        except ValidationError as exc:
            errors.append({"key": key, "error": _validation_message(exc)})
    try:
        for key, action, row in todo:
            if action == "create":
                db.add(row)
                db.flush()
                created.append({"key": key, "service": to_out(row).model_dump()})
            else:
                svc, data = row
                for k, v in data.items():
                    setattr(svc, "metadata_" if k == "metadata" else k, v)
                db.flush()
                updated.append({"key": key, "service": to_out(svc).model_dump()})
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        errors.extend({"key": k, "error": "database error, nothing from this import was saved"} for k, _, _ in todo)
        created, updated = [], []
    return {"created": created, "updated": updated, "errors": errors}
