"""Shared validation for values that end up in links, health probes and widget configs.

One module so that every way of writing a service or a widget (the plain routes, the whole-config
import, page import, discovery) applies the same rules.

Service rules (`SERVICE_RULES`): the link protocol is http or https only (the health check `type`
is separate: http, tcp or ping), the host is a plain hostname or IPv4 address, the path a clean
path, the icon a name / emoji / http(s) URL / local path, and so on. `semantic_errors` reports the
first broken rule (used where a bad value is refused); `repair_service` fixes each broken field in
place and reports it (used for old backups and whole-config imports, which must stay importable).

A path such as `javascript:alert(1)` is accepted and stored as `/javascript:alert(1)`: a path is only
ever appended after `protocol://host`, so it is plain path text, never a scheme.

Widget config URLs (`check_urls` / `neutralise_urls`): any value under a key named url / link / href /
src / customUrl / icon / background_image. `strict` (page files) allows http(s) and relative only;
the default (`strict=False`, plain widget writes and config import) only refuses the dangerous
schemes, because link widgets legitimately use app schemes such as obsidian:// or ssh://.
"""
import copy
import ipaddress
import re
from typing import Any, Optional
from urllib.parse import quote, urlsplit

PROTOCOLS = ("http", "https")
HEALTH_TYPES = ("http", "tcp", "ping")
DEFAULT_PORT = {"http": 80, "https": 443}
MAX_NAME, MAX_CATEGORY, MAX_DESC, MAX_ICON, MAX_PATH, MAX_URL = 100, 60, 300, 200, 600, 2048
CTRL = re.compile(r"[\x00-\x1f\x7f]")


# ---------------------------------------------------------------- field validators
_LABEL = re.compile(r"[A-Za-z0-9_-]{1,63}")


def normalise_host(value: Any) -> Optional[str]:
    """The canonical stored form of a host, or None when it is not acceptable.

    Accepted: hostnames (a trailing dot is kept), IPv4, IPv6 (bare or in brackets; stored bare and
    compressed, `Service.url` adds the brackets) and Unicode (IDN) hostnames, stored IDNA-encoded
    (punycode) so a host is always ASCII. Refused: anything with whitespace, control characters, `@`,
    slashes, backslashes, `?`, `#`, a `host:port` pair or an IPv6 zone id."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not 1 <= len(value) <= 255:
        return None
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
        if ":" not in value:
            return None
    if ":" in value:
        if "%" in value:
            return None
        try:
            return ipaddress.IPv6Address(value).compressed
        except ValueError:
            return None
    try:
        ipaddress.IPv4Address(value)
        return value
    except ValueError:
        pass
    if not value.isascii():
        if re.search(r"[\s\x00-\x1f\x7f@/\\?#%]", value):
            return None
        try:
            value = value.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    trailing_dot = value.endswith(".")
    body = value[:-1] if trailing_dot else value
    if not body or len(body) > 253 or ".." in body or body.startswith(".") or body.startswith("-"):
        return None
    if not all(_LABEL.fullmatch(part) and not part.endswith("-") for part in body.split(".")):
        return None
    return value


def valid_host(value: Any) -> bool:
    return normalise_host(value) is not None


def split_host_port(value: str) -> Optional[tuple[str, int]]:
    """`nas.local:32400` / `[fd00::5]:8080` -> (host, port) when both halves are valid, else None."""
    m = re.fullmatch(r"\[([^\]]+)\]:(\d{1,5})", value.strip()) or re.fullmatch(r"([^:\[\]\s]+):(\d{1,5})", value.strip())
    if not m or not valid_host(m.group(1)) or clean_port(m.group(2)) is None:
        return None
    return normalise_host(m.group(1)), int(m.group(2))


def url_host(host: str) -> str:
    """A host as it is written inside a URL: IPv6 in brackets."""
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


# A path is appended after protocol://host, so it is plain path text. Anything outside this set is
# percent-encoded (spaces, non-ASCII, quotes ...) instead of being refused; `?`, `#` and `%` stay as
# written (Plex, Jellyfin, Proxmox and Portainer all use `#/...` routes).
_PATH_SAFE = "/?#&=+:@%!$'()*,;~-._[]"
_BAD_PATH = re.compile(r"[\x00-\x1f\x7f\\]")


def clean_path(value: str) -> Optional[str]:
    """Normalised, percent-encoded path ("" for none) or None when it is not acceptable
    (control characters, backslashes, too long). Idempotent."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value in ("", "/"):
        return ""
    if _BAD_PATH.search(value) or len(value) > MAX_PATH:
        return None
    value = quote(value, safe=_PATH_SAFE)
    if len(value) > MAX_PATH:
        return None
    return value if value.startswith("/") else "/" + value


def clean_port(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not re.fullmatch(r"\d{1,5}", value):
            return None
        value = int(value)
    return value if isinstance(value, int) and 1 <= value <= 65535 else None


def clean_icon(value: str) -> Optional[str]:
    """A dashboard-icons name, an emoji, an http(s) URL or a /local path; else None."""
    value = value.strip()
    if not value or len(value) > MAX_ICON or CTRL.search(value):
        return None
    if re.match(r"https?://[^\s<>\"']+$", value, re.I) or re.fullmatch(r"/[^\s<>\"':]*", value):
        return value
    value = re.sub(r"\.(png|svg|webp)$", "", value, flags=re.I)  # Homepage writes "sonarr.png"
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.+-]*", value) and not re.match(r"(mdi|si|sh)-", value, re.I):
        return value
    if len(value) <= 20 and not value.isascii() and value.isprintable() and not re.search(r"[<>\"'\s]", value):
        return value  # emoji
    return None


def clean_text(value: str, cap: int) -> tuple[str, bool]:
    """Control characters and runs of spaces collapsed; (text, was_truncated)."""
    text = " ".join(CTRL.sub(" ", value[: cap * 4]).split())
    return (text[:cap].rstrip(), True) if len(text) > cap else (text, False)


def url_userinfo(raw: str) -> bool:
    """True when an http(s) URL carries user[:password]@ credentials."""
    try:
        s = urlsplit(raw.strip())
        return bool(s.username or s.password or "@" in s.netloc)
    except ValueError:
        return False


def parse_url(raw: str, allow_userinfo: bool = False) -> tuple[Optional[dict], str]:
    """An http(s) URL -> {protocol, host, port, path}, or (None, why not).
    Userinfo (user:pw@) is refused unless `allow_userinfo` (a service's own health URL)."""
    raw = raw.strip()
    if len(raw) > MAX_URL:
        return None, "too long"
    if CTRL.search(raw):
        return None, "control characters are not allowed"
    try:
        s = urlsplit(raw)
        port = s.port
    except ValueError:
        return None, "not a valid URL"
    if s.scheme.lower() not in PROTOCOLS:
        return None, "scheme must be http or https"
    if not allow_userinfo and url_userinfo(raw):
        return None, "credentials in a URL are not accepted"
    if port == 0:
        return None, "port must be 1-65535"
    host = normalise_host(s.hostname or "")
    if host is None:
        return None, "host is not a valid hostname or IP address"
    path = clean_path(s.path)
    if path is None:
        return None, "invalid path"
    proto = s.scheme.lower()
    return {"protocol": proto, "host": host, "port": None if port == DEFAULT_PORT[proto] else port, "path": path}, ""


# ---------------------------------------------------------------- service rules
def _icon_ok(v: Any) -> bool:
    return isinstance(v, str) and (v == "" or clean_icon(v) is not None)


def _depends_ok(v: Any) -> bool:
    return isinstance(v, list) and len(v) <= 50 and all(isinstance(i, str) and 0 < len(i) <= 64 for i in v)


def _text(max_len: int):
    return lambda v: isinstance(v, str) and len(v) <= max_len


# (field, ok(value), message, repair(value) -> replacement). A repair satisfies `ok`, except host: an unusable host becomes "" (a service with no address) rather than a guess.
SERVICE_RULES: tuple[tuple[str, Any, str, Any], ...] = (
    ("name", lambda v: isinstance(v, str) and 0 < len(v.strip()) <= MAX_NAME, f"name must be 1-{MAX_NAME} characters",
     lambda v: (str(v).strip()[:MAX_NAME].strip() or "Service")),
    ("protocol", lambda v: v in PROTOCOLS, "protocol must be http or https", lambda v: "http"),
    ("host", valid_host, "host must be a hostname, IPv4 or IPv6 address", lambda v: ""),
    ("port", lambda v: v is None or clean_port(v) is not None, "port must be 1-65535", lambda v: None),
    ("path", lambda v: isinstance(v, str) and clean_path(v) is not None, "path is not valid", lambda v: ""),
    ("icon", _icon_ok, "icon must be an icon name, emoji, http(s) URL or /path", lambda v: ""),
    ("category", _text(MAX_CATEGORY), f"category is longer than {MAX_CATEGORY} characters", lambda v: str(v)[:MAX_CATEGORY]),
    ("description", _text(MAX_DESC), f"description is longer than {MAX_DESC} characters", lambda v: str(v)[:MAX_DESC]),
    ("color", lambda v: isinstance(v, str) and len(v) <= 40 and not re.search(r"[<>\"';]", v), "color is not valid", lambda v: ""),
    ("health_type", lambda v: v in HEALTH_TYPES, "health_type must be http, tcp or ping", lambda v: "http"),
    ("health_url", lambda v: isinstance(v, str) and (v == "" or (len(v) <= MAX_URL and parse_url(v, allow_userinfo=True)[0] is not None)),
     "health_url must be an http(s) URL", lambda v: ""),
    ("health_interval", lambda v: isinstance(v, int) and not isinstance(v, bool) and 5 <= v <= 86400,
     "health_interval must be 5-86400 seconds", lambda v: 30),
    ("depends_on", _depends_ok, "depends_on is not valid",
     lambda v: [i for i in v if isinstance(i, str) and 0 < len(i) <= 64][:50] if isinstance(v, list) else []),
)


def field_errors(fields: dict) -> dict[str, str]:
    """{field: message} for every broken rule among the fields present (None counts as absent)."""
    return {
        key: msg for key, ok, msg, _ in SERVICE_RULES
        if key in fields and fields[key] is not None and not ok(fields[key])
    }


def semantic_errors(fields: dict) -> Optional[str]:
    """The first broken rule as "field: message", or None. Stricter than the service schema."""
    errors = field_errors(fields)
    for key, _, _, _ in SERVICE_RULES:
        if key in errors:
            return f"{key}: {errors[key]}"
    return None


def normalise_service(fields: dict) -> None:
    """Store host and path in their canonical forms (IPv6 bare, IDN as punycode, path percent-encoded,
    leading slash, "/" -> ""). Fields that are not valid are left for the rules to report."""
    if isinstance(fields.get("path"), str):
        fields["path"] = clean_path(fields["path"]) or ""
    if isinstance(fields.get("host"), str):
        fields["host"] = normalise_host(fields["host"]) or fields["host"]
    if isinstance(fields.get("protocol"), str) and fields["protocol"].strip().lower() in PROTOCOLS:
        fields["protocol"] = fields["protocol"].strip().lower()


normalise_path = normalise_service  # old name


def _keep_original(fields: dict, key: str, value: Any) -> None:
    """Keep the replaced value in metadata.import_original.<key> (never overwriting an earlier one)."""
    mkey = "metadata_" if "metadata_" in fields else "metadata"
    meta = fields.get(mkey)
    meta = dict(meta) if isinstance(meta, dict) else {}
    originals = meta.get("import_original")
    originals = dict(originals) if isinstance(originals, dict) else {}
    originals.setdefault(key, value if not isinstance(value, str) else value[:1000])
    meta["import_original"] = originals
    fields[mkey] = meta


def repair_service(fields: dict) -> list[str]:
    """Fix every broken field of a stored/imported service in place; returns one message per fix
    (never the value itself). Used where the data must be kept usable rather than refused.

    Salvage before reset: `host:port` moves the port into the port field (when it is empty or the same;
    a different port in the port field wins), a host is only emptied when it is still not a valid host
    afterwards. Every value that is changed or dropped is kept in metadata.import_original.<field>."""
    notes: list[str] = []
    host = fields.get("host")
    if isinstance(host, str) and not valid_host(host):
        hp = split_host_port(host)
        if hp:
            port_now = fields.get("port")
            if port_now is None or port_now == hp[1]:
                fields["port"] = hp[1]
                note = "the port in the host was moved to the port field"
            else:
                note = "the port in the host was dropped (the port field is set)"
            _keep_original(fields, "host", host)
            fields["host"] = hp[0]
            notes.append(f"host was changed: {note}; the original is kept in metadata.import_original.host")
    normalise_service(fields)
    for key, ok, msg, repair in SERVICE_RULES:
        if key in fields and fields[key] is not None and not ok(fields[key]):
            _keep_original(fields, key, fields[key])
            fields[key] = repair(fields[key])
            notes.append(f"{key} was reset ({msg}); the original is kept in metadata.import_original.{key}")
    normalise_service(fields)
    return notes


# ---------------------------------------------------------------- widget config URLs
# Keys compared case-insensitively. A string anywhere under such a key (a list, a nested object) is
# checked. Out of scope by design: free text (markdown / text widgets) and custom widget code, which
# render sandboxed and are covered by the sandbox and the page-file "contains custom code" warning.
URL_KEYS = {"url", "link", "href", "src", "target", "customurl", "icon", "background_image", "image"}
_SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*):", re.I)
_DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,[A-Za-z0-9+/=]+$", re.I)  # no svg: it can carry script
DANGEROUS_SCHEMES = {"javascript", "vbscript", "data"}


def _url_problem(key: str, value: str, widget_type: str, strict: bool) -> Optional[str]:
    text = re.sub(r"[\x00-\x20\x7f]", "", value)  # browsers ignore these inside a scheme
    m = _SCHEME.match(text)
    if not m:
        return None
    scheme = m.group(1).lower()
    if scheme in ("http", "https"):
        return None
    if scheme == "data" and _DATA_IMAGE.match(text) and (key.lower() == "icon" or (widget_type == "image" and key.lower() == "url")):
        return None
    if strict:
        return f"only http, https or relative addresses are accepted (not {scheme}:)"
    if scheme in DANGEROUS_SCHEMES:
        return f"the {scheme}: scheme is not allowed"
    return None


def _is_url_key(key: Any) -> bool:
    return isinstance(key, str) and key.lower() in URL_KEYS


def _url_strings(node: Any):
    """Every string under a URL-ish key's value (the value itself, list items, object values)."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for v in node:
            yield from _url_strings(v)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _url_strings(v)


def check_urls(value: Any, path: str, widget_type: str, strict: bool = True) -> None:
    """Raise ValueError("<path>.<key>: <why>") for the first unacceptable URL value in a config."""
    if isinstance(value, dict):
        for k, v in value.items():
            if _is_url_key(k):
                for text in _url_strings(v):
                    why = _url_problem(str(k).lower() if str(k).lower() == "icon" else str(k), text, widget_type, strict)
                    if why:
                        raise ValueError(f"{path}.{k}: {why}")
            else:
                check_urls(v, f"{path}.{k}", widget_type, strict)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            check_urls(v, f"{path}[{i}]", widget_type, strict)


def neutralise_urls(value: Any, widget_type: str, path: str = "config") -> tuple[Any, list[str]]:
    """A copy of a config with every unacceptable URL value emptied, and a note per value."""
    notes: list[str] = []

    def clean(node: Any, key: str, where: str) -> Any:
        if isinstance(node, str):
            why = _url_problem(key, node, widget_type, False)
            if why:
                notes.append(f"{where} was emptied ({why})")
                return ""
            return node
        if isinstance(node, list):
            return [clean(v, key, f"{where}[{i}]") for i, v in enumerate(node)]
        if isinstance(node, dict):
            return {k: clean(v, key, f"{where}.{k}") for k, v in node.items()}
        return node

    def walk(node: Any, where: str) -> Any:
        if isinstance(node, dict):
            return {k: (clean(v, k, f"{where}.{k}") if _is_url_key(k) else walk(v, f"{where}.{k}")) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, f"{where}[{i}]") for i, v in enumerate(node)]
        return node

    return walk(copy.deepcopy(value), path), notes
