"""Generic JSON endpoint: GET or POST a URL and expose the response to the Custom status widget.
The type provides neither `stats` nor `items`: the payload is passed through unvalidated, so a top-level
`items` or `stats` key of the upstream JSON is just data. Redirects are followed by hand (up to 5) and the
configured headers go only to the origin that was configured. The body is capped at 1 MB."""
import json

from ..base import Context, Spec
from ..net import read_capped, stream_follow

MAX_BYTES = 1_000_000


async def fetch(ctx: Context):
    method = str(ctx.cfg.get("method") or "GET").upper()
    async with stream_follow(ctx.client, method, ctx.raw_url(), headers=ctx.cfg.get("headers") or {}) as resp:
        resp.raise_for_status()
        body = await read_capped(resp, MAX_BYTES, message="response is too large (over 1 MB)")
        encoding = resp.encoding or "utf-8"
    try:
        return json.loads(body)
    except ValueError:
        return {"text": body.decode(encoding, errors="replace")}


SPEC = Spec(
    type="json",
    label="JSON endpoint",
    description="Any URL that returns JSON; pick values with the Custom status widget.",
    fetch=fetch,
    secret_url=True,  # the URL path may be the credential
    fields=[
        {"key": "url", "label": "URL", "type": "url", "required": True, "placeholder": "http://192.168.1.25:8080/status.json"},
        {"key": "method", "label": "Method", "type": "select", "default": "GET",
         "options": [{"value": "GET", "label": "GET"}, {"value": "POST", "label": "POST"}]},
        {"key": "headers", "label": "Headers (one per line: Name: value)", "type": "headers", "secret": True,
         "placeholder": "Authorization: Bearer …"},
    ],
)
