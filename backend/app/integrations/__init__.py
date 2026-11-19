"""Integrations: the backend polls external APIs and caches the result, so the frontend never
deals with CORS, sockets or credentials. One module per type; the package does the rest.

ADDING A TYPE (the whole job)
  1. Create `types/<name>.py` that defines `SPEC = Spec(...)`. Nothing else is registered
     anywhere: the module is found automatically (registry.discover), the form, the type
     picker, validation, secret masking, `/api/integrations/types` and the MCP tools follow.
  2. Write a fixture test in `backend/tests/` with `integration_utils.run_fetch` (below).
  3. Optional: a widget in `frontend/src/widgets/` (the generic `integration_stats` and
     `integration_list` widgets already render anything that returns `stats` / `items`).
  4. Add a row to the README integrations table. Run the three suites.

SPEC (`base.Spec`)
  type              key, [a-z][a-z0-9_]*, stored in the database: never rename it
  label, description   UI name and one line for the type picker
  fetch             `async def fetch(ctx) -> dict`; raise on failure (see ERRORS)
  fields            [{key, label, type, secret?, required?, default?, placeholder?, help?, options?,
                      min?, max?, schemes?, pattern?, rows?}]
                    type: text | url | number | checkbox | select | textarea | secret | headers
                    (the frontend ConfigForm vocabulary; `url` is validated as http(s), or `schemes`;
                    `headers` is a {name: value} dict). `secret: True` makes the value write-only.
                    `verify_tls` (default ON; new rows verify certificates) and `ca_cert` (optional PEM, trusted
                    in addition to the system CAs) are added for every type with `uses_http`.
                    `max_length` caps a text/textarea value.
  default_interval  seconds between polls for new integrations (>= 5)
  provides          ("stats",), ("items",) or both: which normalised keys `fetch` returns; generic
                    widgets offer only integrations whose type provides what they need
  actions           {name: Action(fn, label, description, payload=[fields])}; `fn(ctx, payload)`;
                    the payload is validated against its fields first
  uses_http         False for types with no HTTP (host)
  client_factory    optional `(ctx) -> httpx.AsyncClient` (unix sockets)
  validate          optional `(cfg) -> [(field_key, message)]` cross-field check
  close             optional `async (ctx)`: called once when the context is closed (edit, delete, disable,
                    shutdown, end of /test): log out of the upstream session (Pi-hole, UniFi, qBittorrent)
  secret_url        True when the URL path is itself a credential (ics, rss, json): logs keep the host only

CONTEXT (`base.Context`, the argument of fetch and actions)
  ctx.cfg            config with the spec's defaults filled in
  ctx.client         one shared httpx.AsyncClient per integration (8 s timeout, verify from `verify_tls` / `ca_cert`);
                     do not close it, do not create your own
  ctx.state          dict kept between polls (session ids, tokens); cleared when the config changes
  ctx.base_url(key="url")   URL without trailing slash, `http://` added when no scheme
  ctx.url(path, key="url")  base_url + path;  ctx.raw_url(key)  as typed (keeps a trailing slash)
  ctx.auth_gate(msg) / ctx.auth_fail(msg) / ctx.auth_ok()   login backoff. A type that logs in calls
                     `auth_gate(msg)` before the login request (raises AuthFailed with NO request while a
                     backoff runs), `raise ctx.auth_fail(msg)` when the login or a post-login request is
                     refused (60 s, doubling, at most 15 min); the runtime resets it after a good fetch.
                     Calls a person triggers (Test, refresh, actions) bypass the gate. A static key that
                     gets a 401/403 should raise AuthFailed (or let raise_for_status raise): the poll
                     interval then stretches up to x10.
  net.stream_follow / net.read_capped   use these instead of `follow_redirects=True` and unbounded reads:
                     redirects are followed by hand (max 5) and credentials / custom headers go only to the
                     original origin; read_capped limits DECODED bytes (zip-bomb safe)
  base.cached(ctx, key, ttl, loader)   at most one slow upstream call per ttl

NORMALISED SHAPE (`base.Normalised`; validated after every fetch)
  A result is a dict. Besides any type-specific keys it may carry:
    "stats": [{"key", "label", "value": int|float|str|bool|None, "unit"?, "tone"?: ok|warn|bad|info,
               "series"?: [float, ...]}]
    "items": [{"title", "subtitle"?, "status"?: online|offline|degraded|maintenance|unknown,
               "progress"?: 0-100, "meta"?, "url"?, "icon"?,
               "ref"?: {key: str|int}}]   ref = stable ids an action needs for this row (e.g. proxmox
                                          {node, vmid, kind}); widgets use it instead of parsing text
  Build stats with `base.stat(key, label, value, unit, tone, series)`.

ERRORS
  Raise freely: the runtime turns any exception into a short safe message (HTTP status and
  reason, "timeout", "connection refused", "TLS error", "invalid response") with the host and
  no path, query or userinfo; guests see "Unavailable". Never put a URL, token or response body
  in a message you write: raise `errors.IntegrationError("login failed")` for your own wording.

SECRETS
  Fields with `secret: True` are write-only over the API: lists and create/patch responses show
  the mask for a set value and "" for an unset one; PATCH with the mask (or no key) keeps the
  stored value, "" clears. Exports and backups keep real values. A secret whose value equals the mask
  string cannot be stored (it always means "keep the stored value"). Surrounding whitespace of secrets,
  text and header values is stripped and control characters are refused (422, never echoing the value);
  rows stored before that are stripped when the context is built.
  Never log or raise text built from headers, bodies or exceptions that embed them: the runtime logs
  httpx protocol errors and validation errors by class name only.

FIXTURE TEST
  `backend/tests/integration_utils.py`: `run_fetch(type, config, routes, state=None)` runs the
  type's fetch on an httpx.MockTransport built from `{(method, url-pattern): response}` and checks
  the result against the normalised shape. See test_integration_types.py for examples.

CHECKLIST
  [ ] SPEC with label, one-line description, fields (secret / required / default set), provides
  [ ] fetch raises on failure, returns the normalised keys it declares in `provides`
  [ ] no URL, key or body in your own error text; secrets only in fields marked secret
  [ ] ctx.client / ctx.state used (no per-fetch client); long-lived tokens kept in ctx.state
  [ ] fixture test with a realistic upstream response, plus one failure case
  [ ] actions (if any) declare their payload fields
  [ ] README table row; `npm run catalog` is NOT needed (it covers widgets, not types)
"""
from .base import Action, Context, Normalised, Spec, check_shape, stat
from .errors import IntegrationError, safe_error
from .registry import SPECS, TYPES, discover
from .runtime import (
    ActionFailed,
    InvalidPayload,
    UnknownAction,
    call,
    close_all,
    close_pending,
    fetch,
    get,
    get_all,
    interval_of,
    invalidate,
    refresh,
    refresh_row,
    run_action,
    snapshot,
    start,
    stop,
    test_config,
)

__all__ = [
    "Action", "ActionFailed", "Context", "IntegrationError", "InvalidPayload", "Normalised", "SPECS", "Spec",
    "TYPES", "call", "UnknownAction", "check_shape", "close_all", "close_pending", "discover", "fetch",
    "get", "get_all", "interval_of", "invalidate", "refresh", "refresh_row", "run_action", "safe_error",
    "snapshot", "start", "stop", "stat", "test_config",
]
