"""RSS / Atom feed (feedparser, lazy import)."""
import asyncio
import calendar
import io

from ..base import Context, Spec
from ..net import read_capped, stream_follow

RSS_MAX_BYTES = 5_000_000


async def fetch(ctx: Context) -> dict:
    # Redirects are followed by hand and the stream is capped on DECODED bytes (net.read_capped);
    # an oversized feed is truncated, never rejected.
    async with stream_follow(ctx.client, "GET", ctx.raw_url()) as resp:
        resp.raise_for_status()
        body = await read_capped(resp, RSS_MAX_BYTES, truncate=True)
    import feedparser  # lazy: missing dep = error tile, not backend outage

    # Parsing is CPU-bound and unbounded in time, so it must not run on the event loop
    # (ADR 006). The 5 MB cap keeps a huge or entity-amplified feed proportionate; it
    # caps the download too (streamed, truncated, never rejected).
    # feedparser.parse(str|bytes) treats its argument as a URL, a file PATH or data, in that order: a body
    # that is a path would open a local file. A file-like object is only ever .read(): no path, no URL.
    parsed = await asyncio.to_thread(feedparser.parse, io.BytesIO(body))
    entries = []
    for e in parsed.entries[:30]:
        when = e.get("published_parsed")
        entries.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            # feedparser parses dates as UTC; epoch seconds keep it JSON-safe (struct_time is not).
            "published_parsed": calendar.timegm(when) if when else None,
            "summary": (e.get("summary") or "")[:500],
        })
    return {
        "title": parsed.feed.get("title", ""),
        "entries": entries,
        "items": [
            {
                "title": e["title"] or "(untitled)",
                **({"url": e["link"]} if e["link"].lower().startswith(("http://", "https://")) else {}),
            }
            for e in entries
        ],
    }


SPEC = Spec(
    type="rss",
    label="RSS / Atom",
    description="Headlines from an RSS or Atom feed.",
    fetch=fetch,
    default_interval=300,
    provides=("items",),
    secret_url=True,  # private feed addresses often carry a token in the path
    fields=[
        {"key": "url", "label": "Feed URL", "type": "url", "required": True, "placeholder": "https://example.com/feed.xml"},
    ],
)
