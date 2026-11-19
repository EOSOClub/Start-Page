"""Home Assistant (REST API, long-lived access token): states of a hand-picked list of entities, plus
guarded toggle / turn_on / turn_off / press actions.

Config `entities`: one per line, `entity_id` or `Label = entity_id` (blank lines and `# comments` ignored,
at most 50, entity ids strictly `domain.object_id` in lowercase).

FETCH STRATEGY: up to 10 entities -> one GET /api/states/<entity_id> each (concurrent; small payloads).
More than 10 -> a single GET /api/states filtered locally (one request instead of many, at the cost of
transferring every entity once; that is cheaper than 11+ round trips on a typical install).
A 404 for one entity marks it unknown ("not found"); other per-entity errors do too, unless every entity
failed that way, in which case the first error is raised. A 401 always fails the fetch.

RESULT
  stats     one per sensor-like entity (domain sensor / input_number / number / counter, or any entity with a
            unit_of_measurement): key = entity_id, value = number (or text), unit; then `active` (entities that
            are on / home / open / playing) and `total`.
  items     one per entity: title = label or friendly_name, subtitle = entity_id, meta = "state unit",
            status online (on, home, open, opening, playing) / offline (off, away, not_home, closed, closing) /
            unknown (unavailable, unknown, missing) / absent for anything else.
  entities  [{"entity_id", "label", "domain", "state", "unit", "toggleable", "attributes"}]; `state` is the
            raw state string ("unknown" for a missing entity), `unit` str|None, `toggleable` true for domains
            light, switch, input_boolean, fan, cover, automation, media_player; `attributes` holds only the
            keys present of device_class, brightness, current_temperature, temperature, icon.

ACTIONS (payload {"entity_id"}); an action targets only an entity that is listed in this integration's
`entities` AND whose domain is in ACTION_DOMAINS (light, switch, input_boolean, fan, cover, scene, script,
button, automation, media_player). Everything else (lock, alarm_control_panel, ...) is refused before any
request is made. toggle / turn_on / turn_off call homeassistant.<service> (not offered for button and scene,
which have no on/off); press calls button.press for a button and <domain>.turn_on for a scene or script.
"""
import asyncio
import re
from typing import Any, Optional

import httpx

from ..base import Action, Context, Spec, stat
from ..errors import AuthFailed, IntegrationError

MAX_ENTITIES = 50
BULK_THRESHOLD = 10
ENTITY_ID = re.compile(r"[a-z][a-z0-9_]*\.[a-z0-9_]+")
ACTION_DOMAINS = {"light", "switch", "input_boolean", "fan", "cover", "scene", "script", "button", "automation", "media_player"}
TOGGLE_DOMAINS = {"light", "switch", "input_boolean", "fan", "cover", "automation", "media_player"}
ONOFF_DOMAINS = TOGGLE_DOMAINS | {"script"}
PRESS_DOMAINS = {"button", "scene", "script"}
SENSOR_DOMAINS = {"sensor", "input_number", "number", "counter"}
ATTRIBUTES = ("device_class", "brightness", "current_temperature", "temperature", "icon")
_ON = {"on", "home", "open", "opening", "playing"}
_OFF = {"off", "away", "not_home", "closed", "closing"}
_UNKNOWN = {"unavailable", "unknown"}


def parse_entities(text: str) -> list[dict]:
    """[{"entity_id", "label"|None}]. Raises ValueError("line N: ...")."""
    out, seen = [], set()
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        label, sep, ident = line.rpartition("=") if "=" in line else ("", "", line)
        label, ident = label.strip(), ident.strip()
        if not ENTITY_ID.fullmatch(ident) or (sep and not label):
            raise ValueError(f"line {n}: expected `entity_id` or `Label = entity_id` (e.g. light.kitchen)")
        if ident not in seen:
            seen.add(ident)
            out.append({"entity_id": ident, "label": label or None})
    if len(out) > MAX_ENTITIES:
        raise ValueError(f"at most {MAX_ENTITIES} entities")
    return out


def _validate(cfg: dict) -> list[tuple[str, str]]:
    try:
        if not parse_entities(cfg.get("entities", "")):
            return [("entities", "add at least one entity, one per line")]
    except ValueError as exc:
        return [("entities", str(exc))]
    return []


def _headers(ctx: Context) -> dict:
    return {"Authorization": f"Bearer {ctx.cfg.get('token') or ''}", "Content-Type": "application/json"}


def _check(resp: httpx.Response) -> None:
    if resp.status_code in (401, 403):
        raise AuthFailed("login failed: check the Home Assistant access token")
    resp.raise_for_status()


async def _one(ctx: Context, entity_id: str) -> Any:
    """The state object, None when the entity does not exist, or the exception for another failure."""
    try:
        resp = await ctx.client.get(ctx.url(f"/api/states/{entity_id}"), headers=_headers(ctx))
        if resp.status_code == 404:
            return None
        _check(resp)
        return resp.json()
    except IntegrationError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        return exc


def _number(raw: Any) -> Any:
    try:
        f = float(raw)
    except (TypeError, ValueError):
        return raw
    if f != f or f in (float("inf"), float("-inf")):
        return raw
    return int(f) if f.is_integer() and "." not in str(raw) else round(f, 2)


def _status(state: Optional[str]) -> Optional[str]:
    s = (state or "").lower()
    return "online" if s in _ON else "offline" if s in _OFF else "unknown" if s in _UNKNOWN else None


def _entity(spec_line: dict, obj: Any) -> dict:
    entity_id = spec_line["entity_id"]
    domain = entity_id.split(".", 1)[0]
    if isinstance(obj, dict):
        attrs = obj.get("attributes") if isinstance(obj.get("attributes"), dict) else {}
        state = str(obj.get("state") if obj.get("state") is not None else "unknown")
        found = True
    else:
        attrs, state, found = {}, "unknown", False
    unit = attrs.get("unit_of_measurement")
    return {
        "entity_id": entity_id,
        "label": spec_line["label"] or attrs.get("friendly_name") or entity_id,
        "domain": domain,
        "state": state,
        "unit": unit if isinstance(unit, str) and unit else None,
        "toggleable": domain in TOGGLE_DOMAINS,
        "attributes": {k: attrs[k] for k in ATTRIBUTES if k in attrs},
        "_found": found,
    }


async def fetch(ctx: Context) -> dict:
    wanted = parse_entities(ctx.cfg.get("entities", ""))
    if not wanted:
        raise IntegrationError("no entities configured")
    if len(wanted) > BULK_THRESHOLD:
        resp = await ctx.client.get(ctx.url("/api/states"), headers=_headers(ctx))
        _check(resp)
        everything = resp.json()
        by_id = {o.get("entity_id"): o for o in everything if isinstance(o, dict)} if isinstance(everything, list) else {}
        results = [by_id.get(w["entity_id"]) for w in wanted]
    else:
        tasks = [asyncio.ensure_future(_one(ctx, w["entity_id"])) for w in wanted]
        try:
            results = list(await asyncio.gather(*tasks))
        except BaseException:  # one request failed hard (bad token): stop its siblings
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        failures = [r for r in results if isinstance(r, Exception)]
        if len(failures) == len(results):
            raise failures[0]
        results = [None if isinstance(r, Exception) else r for r in results]
    entities = [_entity(w, r) for w, r in zip(wanted, results)]
    stats, items = [], []
    for e in entities:
        found = e.pop("_found")
        status = _status(e["state"]) if found else "unknown"
        if found and (e["unit"] or e["domain"] in SENSOR_DOMAINS):
            value = None if e["state"].lower() in _UNKNOWN else _number(e["state"])
            stats.append(stat(e["entity_id"], e["label"], value, e["unit"]))
        meta = "not found" if not found else " ".join(x for x in (e["state"], e["unit"]) if x)
        item = {"title": e["label"], "subtitle": e["entity_id"], "meta": meta}
        if status:
            item["status"] = status
        items.append(item)
    active = sum(1 for i in items if i.get("status") == "online")
    stats.append(stat("active", "Active", active))
    stats.append(stat("total", "Entities", len(entities)))
    return {"stats": stats, "items": items, "entities": entities}


# ---------------------------------------------------------------- actions
def _target(ctx: Context, payload: dict, allowed: set[str]) -> tuple[str, str]:
    """(entity_id, domain) of a safe target, or IntegrationError before any request is made."""
    entity_id = payload.get("entity_id")
    if not isinstance(entity_id, str) or not ENTITY_ID.fullmatch(entity_id):
        raise IntegrationError("invalid entity id")
    try:
        configured = {w["entity_id"] for w in parse_entities(ctx.cfg.get("entities", ""))}
    except ValueError:
        configured = set()
    if entity_id not in configured:
        raise IntegrationError("entity is not in this integration's entity list")
    domain = entity_id.split(".", 1)[0]
    if domain not in ACTION_DOMAINS or domain not in allowed:
        raise IntegrationError(f"actions on {domain} entities are not allowed")
    return entity_id, domain


async def _call(ctx: Context, domain: str, service: str, entity_id: str) -> dict:
    resp = await ctx.client.post(ctx.url(f"/api/services/{domain}/{service}"), json={"entity_id": entity_id},
                                 headers=_headers(ctx))
    _check(resp)
    return {"entity_id": entity_id, "service": f"{domain}.{service}"}


def _service_action(service: str):
    async def run(ctx: Context, payload: dict) -> dict:
        entity_id, _ = _target(ctx, payload, ONOFF_DOMAINS)
        return await _call(ctx, "homeassistant", service, entity_id)
    return run


async def press(ctx: Context, payload: dict) -> dict:
    entity_id, domain = _target(ctx, payload, PRESS_DOMAINS)
    return await _call(ctx, "button", "press", entity_id) if domain == "button" else await _call(ctx, domain, "turn_on", entity_id)


_PAYLOAD = [{"key": "entity_id", "label": "Entity", "type": "text", "required": True, "pattern": ENTITY_ID.pattern}]

SPEC = Spec(
    type="home_assistant",
    label="Home Assistant",
    description="States of chosen Home Assistant entities, with toggle, on/off and press actions for safe domains.",
    fetch=fetch,
    default_interval=30,
    provides=("stats", "items"),
    validate=_validate,
    fields=[
        {"key": "url", "label": "Home Assistant URL", "type": "url", "required": True, "placeholder": "http://homeassistant.local:8123"},
        {"key": "token", "label": "Long-lived access token", "type": "secret", "required": True,
         "help": "Profile -> Security -> Long-lived access tokens."},
        {"key": "entities", "label": "Entities (one per line: entity_id or Label = entity_id)", "type": "textarea",
         "required": True, "rows": 6,
         "placeholder": "light.kitchen\nLiving room = switch.living_room_lamp\nsensor.outside_temperature",
         "help": "Only entities listed here can be controlled, and only lights, switches, fans, covers, scenes, "
                 "scripts, buttons, automations, input booleans and media players. Locks and alarms never can."},
    ],
    actions={
        "toggle": Action(_service_action("toggle"), "Toggle", "Toggle a listed light, switch, fan, cover, ...", _PAYLOAD),
        "turn_on": Action(_service_action("turn_on"), "Turn on", "Turn a listed entity on.", _PAYLOAD),
        "turn_off": Action(_service_action("turn_off"), "Turn off", "Turn a listed entity off.", _PAYLOAD),
        "press": Action(press, "Press", "Press a listed button or run a listed scene or script.", _PAYLOAD),
    },
)
