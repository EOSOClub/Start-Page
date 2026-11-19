"""iCalendar (.ics) feed: upcoming events with a small hand-written parser (no dependencies).

Source: http(s):// or webcal:// (rewritten to https://), optional basic auth. The body is streamed and an
over-size feed (> MAX_BYTES, 5 MB) is refused with a clear error rather than parsed half.
Parsed: line unfolding, VEVENT (nested components such as VALARM are ignored), DTSTART / DTEND as date,
UTC date-time (Z), TZID=<IANA name> date-time or floating (floating and unknown / non-IANA TZIDs, e.g.
Windows zone names, are read in the configured time zone), DURATION, SUMMARY / LOCATION / DESCRIPTION with
escapes (\\n \\, \\; \\\\), STATUS:CANCELLED skipped.
Recurrence: RRULE FREQ=DAILY|WEEKLY|MONTHLY|YEARLY with INTERVAL, COUNT, UNTIL, BYDAY (weekly weekdays; monthly
and yearly with ordinals such as 2TU / -1FR), BYMONTHDAY (lists and negatives), BYMONTH (yearly), BYSETPOS
(applied within each month), EXDATE (date or date-time, several per line), and RECURRENCE-ID overrides (the
override replaces that occurrence). Expansion is done in the event's own wall-clock time, so daylight saving
changes keep the local hour. NOT supported (ignored, so the series may show wrong or extra dates): RDATE,
BYWEEKNO, BYYEARDAY, BYHOUR/BYMINUTE/BYSECOND, WKST (weeks start Monday), FREQ=HOURLY/MINUTELY/SECONDLY (the
event is shown once), BYDAY / BYMONTH combined in ways beyond those above, VTIMEZONE definitions (only IANA names).
Expansion can never run away: per-rule step cap (MAX_STEPS), a shared step budget (MAX_TOTAL_STEPS), a cap per
event (MAX_PER_EVENT) and a total event cap (MAX_EXPANDED); nothing raises on a bad rule or event, the event
is skipped or shown once.

RESULT
  items   upcoming or running events (end after now, start within `days_ahead` days of the start of today),
          sorted by start, at most `max_events`: title, subtitle = location, meta = compact time ("Today 14:30",
          "Tomorrow", "Now, until 15:30", "Until Fri 6 Jun"), status "online" while the event is running.
  stats   events_today and events_week (events overlapping today / the next 7 days including today, counting
          ones already finished today), next_event (title of the first running or upcoming event, None when
          there is none), next_time (the same compact time text, None when none).
  events  [{"title", "start", "end", "all_day", "location", "description"}] for an agenda widget: the same events
          as `items` (same order, same cap). `start` / `end` are ISO 8601 strings with a UTC offset in the
          configured time zone ("2025-06-02T14:30:00+01:00"); for an all-day event `start` is local midnight
          of its first day and `end` is local midnight AFTER its last day (exclusive, as in iCalendar);
          `location` and `description` are str or None (description is at most 300 characters).
  timezone  the configured IANA name the dates were converted to.
"""
import asyncio
import calendar
import re
import threading
import time as _timemod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone, tzinfo
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

import httpx

from ..base import Context, Spec, stat
from ..errors import AuthFailed, IntegrationError
from ..net import read_capped, stream_follow

MAX_BYTES = 5_000_000
MAX_VEVENTS = 5000
MAX_EXPANDED = 2000
MAX_PER_EVENT = 500
MAX_STEPS = 20_000
MAX_TOTAL_STEPS = 200_000
MAX_LINE = 20_000  # characters of one logical (unfolded) line; the rest is dropped
MAX_BYDAY, MAX_BYMONTH, MAX_BYMONTHDAY, MAX_BYSETPOS = 100, 12, 62, 100
PARSE_DEADLINE = 3.0  # wall-clock seconds for the whole parse + expansion, then "calendar too complex"
FREQS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def _now() -> datetime:
    """The clock (aware, UTC); tests replace this."""
    return datetime.now(timezone.utc)


_monotonic = _timemod.monotonic  # tests replace this to trigger the deadline without waiting
# Parsing is CPU-bound Python: it runs on its own two threads so a hostile feed can never starve the
# default executor that `host` and `rss` use. The deadline makes the thread stop by itself.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ics-parse")


class _Deadline:
    """Wall-clock limit checked inside every loop of the parser; also stops when `cancel` is set
    (the awaiting fetch was cancelled), so an abandoned parse does not keep burning CPU."""

    def __init__(self, seconds: float = PARSE_DEADLINE, cancel: Optional[threading.Event] = None):
        self.limit = _monotonic() + seconds
        self.cancel = cancel
        self.truncated = False  # some events were cut off by a cap (see build()'s "truncated")

    def check(self) -> None:
        if _monotonic() > self.limit or (self.cancel is not None and self.cancel.is_set()):
            raise IntegrationError("calendar too complex")


# ---------------------------------------------------------------- zones
def _zone_or_none(name: Optional[str]) -> Optional[tzinfo]:
    name = (name or "").strip()
    if not name:
        return None
    if name.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc
    for candidate in (name, "/".join(name.split("/")[-2:])):
        try:
            return ZoneInfo(candidate)
        except (KeyError, ValueError, OSError):
            continue
    return None


def _zone(name: Optional[str]) -> tzinfo:
    zone = _zone_or_none(name or "UTC")
    if zone is None:
        raise IntegrationError("unknown time zone (use an IANA name such as Europe/London; "
                               "on Windows the tzdata package must be installed)")
    return zone


def _validate(cfg: dict) -> list[tuple[str, str]]:
    if _zone_or_none(cfg.get("timezone") or "UTC") is None:
        return [("timezone", "unknown time zone (use an IANA name such as Europe/London)")]
    return []


# ---------------------------------------------------------------- tokenising
def _unfold(text: str, deadline: Optional[_Deadline] = None) -> list[str]:
    """Logical lines: continuation lines are collected and joined once (never `+=` in a loop), and a
    logical line is cut at MAX_LINE characters, so a line folded tens of thousands of times stays cheap."""
    lines: list[str] = []
    parts: list[str] = []
    size = 0

    def flush() -> None:
        if parts:
            lines.append("".join(parts))

    for n, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")):
        if deadline is not None and n % 4096 == 0:
            deadline.check()
        if raw[:1] in (" ", "\t") and parts:
            if size < MAX_LINE:
                piece = raw[1:MAX_LINE - size]
                parts.append(piece)
                size += len(piece)
        elif raw:
            flush()
            piece = raw[:MAX_LINE]
            parts, size = [piece], len(piece)
    flush()
    return lines


def _split_quoted(s: str, sep: str) -> list[str]:
    parts, cur, quoted = [], [], False
    for ch in s:
        if ch == '"':
            quoted = not quoted
        if ch == sep and not quoted:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _split_prop(line: str) -> Optional[tuple[str, dict, str]]:
    quoted, colon = False, -1
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            colon = i
            break
    if colon < 1:
        return None
    parts = _split_quoted(line[:colon], ";")
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.strip().upper()] = v.strip().strip('"')
    return parts[0].strip().upper(), params, line[colon + 1:]


def _vevents(lines: list[str], deadline: Optional[_Deadline] = None) -> list[dict]:
    events: list[dict] = []
    cur: Optional[dict] = None
    depth = 0
    for n, line in enumerate(lines):
        if deadline is not None and n % 1024 == 0:
            deadline.check()
        p = _split_prop(line)
        if not p:
            continue
        name, params, value = p
        if name == "BEGIN":
            if cur is None:
                if value.strip().upper() == "VEVENT":
                    cur, depth = {}, 0
            else:
                depth += 1
        elif name == "END":
            if cur is None:
                continue
            if depth > 0:
                depth -= 1
            elif value.strip().upper() == "VEVENT":
                events.append(cur)
                cur = None
                if len(events) >= MAX_VEVENTS:
                    if deadline is not None:
                        deadline.truncated = True
                    break
        elif cur is not None and depth == 0:
            cur.setdefault(name, []).append((params, value))
    return events


def _unescape(s: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), s)


def _text(ev: dict, name: str, limit: int) -> Optional[str]:
    if name not in ev:
        return None
    out = _unescape(ev[name][0][1]).strip()
    return out[:limit] or None


# ---------------------------------------------------------------- times
_DT = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?)?(Z)?$")
_DUR = re.compile(r"^([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _parse_time(value: str, params: dict, local: tzinfo) -> tuple[datetime, bool, tzinfo]:
    """(aware datetime, is_date_only, tz). Raises ValueError. A date is local midnight."""
    m = _DT.match(value.strip().upper())
    if not m:
        raise ValueError("bad date")
    y, mo, d, hh, mi, ss, z = m.groups()
    if hh is None or params.get("VALUE", "").upper() == "DATE":
        return datetime(int(y), int(mo), int(d), tzinfo=local), True, local
    tz: tzinfo = timezone.utc if z else (_zone_or_none(params.get("TZID")) or local)
    return datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss or 0), tzinfo=tz), False, tz


def _parse_duration(value: str) -> timedelta:
    m = _DUR.match(value.strip().upper())
    if not m:
        raise ValueError("bad duration")
    sign, w, d, h, mi, s = m.groups()
    td = timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return -td if sign == "-" else td


@dataclass
class _Base:
    start: datetime  # aware, in `tz`
    tz: tzinfo
    all_day: bool
    dur: timedelta

    @property
    def wall(self) -> datetime:
        return self.start.replace(tzinfo=None)

    def end_of(self, start: datetime) -> datetime:
        if self.all_day:
            return (start.replace(tzinfo=None) + self.dur).replace(tzinfo=self.tz)
        return (start.astimezone(timezone.utc) + self.dur).astimezone(self.tz)


def _base(ev: dict, local: tzinfo) -> Optional[_Base]:
    if "DTSTART" not in ev:
        return None
    params, value = ev["DTSTART"][0]
    start, all_day, tz = _parse_time(value, params, local)
    dur: Optional[timedelta] = None
    if "DTEND" in ev:
        eparams, evalue = ev["DTEND"][0]
        end, _, _ = _parse_time(evalue, eparams, local)
        dur = (end.date() - start.date()) if all_day else end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
    elif "DURATION" in ev:
        dur = _parse_duration(ev["DURATION"][0][1])
    if all_day:
        days = dur.days if dur is not None else 1
        dur = timedelta(days=max(days, 1))
    else:
        dur = max(dur or timedelta(0), timedelta(0))
    return _Base(start, tz, all_day, dur)


# ---------------------------------------------------------------- recurrence
@dataclass
class _Rule:
    freq: str
    interval: int = 1
    count: Optional[int] = None
    until_raw: Optional[str] = None
    byday: list = field(default_factory=list)  # [(ordinal|None, weekday)]
    bymonthday: list = field(default_factory=list)
    bymonth: list = field(default_factory=list)
    bysetpos: list = field(default_factory=list)
    wdset: frozenset = frozenset()  # weekdays of byday, computed once in _parse_rule
    wds: list = field(default_factory=list)  # the same, sorted


_BYDAY = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")  # one short token: no backtracking risk


def _uniq(values: list, cap: int) -> list:
    return list(dict.fromkeys(values))[:cap]


def _ints(raw: Optional[str], lo: int, hi: int) -> list[int]:
    out = []
    for p in (raw or "").split(",")[:1000]:
        try:
            v = int(p)
        except ValueError:
            continue
        if lo <= v <= hi and v != 0:
            out.append(v)
    return out


def _parse_rule(value: str) -> Optional[_Rule]:
    parts: dict[str, str] = {}
    for seg in value.split(";"):
        k, _, v = seg.partition("=")
        parts[k.strip().upper()] = v.strip()
    freq = parts.get("FREQ", "").upper()
    if freq not in FREQS:
        return None
    rule = _Rule(freq=freq)
    try:
        rule.interval = min(max(int(parts.get("INTERVAL") or 1), 1), 1000)
    except ValueError:
        pass
    try:
        if parts.get("COUNT"):
            rule.count = max(int(parts["COUNT"]), 1)
    except ValueError:
        pass
    rule.until_raw = parts.get("UNTIL") or None
    seen: set = set()
    for token in (parts.get("BYDAY") or "").split(",")[:MAX_BYDAY * 4]:
        m = _BYDAY.match(token.strip().upper())
        if m:
            entry = (int(m.group(1)) if m.group(1) else None, _WEEKDAYS[m.group(2)])
            if entry not in seen and len(seen) < MAX_BYDAY:  # de-duplicated and capped
                seen.add(entry)
                rule.byday.append(entry)
    rule.wdset = frozenset(wd for _, wd in rule.byday)
    rule.wds = sorted(rule.wdset)
    rule.bymonthday = _uniq(_ints(parts.get("BYMONTHDAY"), -31, 31), MAX_BYMONTHDAY)
    rule.bymonth = sorted(set(m for m in _ints(parts.get("BYMONTH"), 1, 12) if m > 0))[:MAX_BYMONTH]
    rule.bysetpos = _uniq(_ints(parts.get("BYSETPOS"), -366, 366), MAX_BYSETPOS)
    return rule


def _month_walls(y: int, m: int, rule: _Rule, d0: datetime) -> list[datetime]:
    n = calendar.monthrange(y, m)[1]
    valid_md = {d if d > 0 else n + 1 + d for d in rule.bymonthday if 1 <= (d if d > 0 else n + 1 + d) <= n}
    if rule.byday:
        days: set[int] = set()
        for ordinal, wd in rule.byday:
            ds = [d for d in range(1, n + 1) if calendar.weekday(y, m, d) == wd]
            if ordinal is None:
                days.update(ds)
            elif 1 <= abs(ordinal) <= len(ds):
                days.add(ds[ordinal - 1 if ordinal > 0 else ordinal])
        if rule.bymonthday:
            days &= valid_md
    elif rule.bymonthday:
        days = valid_md
    else:
        days = {d0.day} if d0.day <= n else set()  # day 29-31 skips shorter months, as in RFC 5545
    chosen = sorted(days)
    if rule.bysetpos:
        picks = {p - 1 if p > 0 else p for p in rule.bysetpos if 1 <= abs(p) <= len(chosen)}
        chosen = sorted(chosen[i] for i in picks)
    return [datetime(y, m, d, d0.hour, d0.minute, d0.second) for d in chosen]


def _period(rule: _Rule, d0: datetime, k: int) -> tuple[datetime, list[datetime]]:
    """(naive start of period k, naive candidate wall times in it, ascending). May raise ValueError/OverflowError."""
    t = d0.time()
    day0 = d0.date()
    if rule.freq == "DAILY":
        d = day0 + timedelta(days=k * rule.interval)
        ok = not rule.wdset or d.weekday() in rule.wdset
        w = datetime.combine(d, t)
        return w, [w] if ok else []
    if rule.freq == "WEEKLY":
        week = day0 - timedelta(days=day0.weekday()) + timedelta(weeks=k * rule.interval)
        wds = rule.wds or [day0.weekday()]
        return datetime.combine(week, t), [datetime.combine(week + timedelta(days=wd), t) for wd in wds]
    if rule.freq == "MONTHLY":
        y, m0 = divmod(day0.year * 12 + day0.month - 1 + k * rule.interval, 12)
        return datetime(y, m0 + 1, 1), _month_walls(y, m0 + 1, rule, d0)
    y = day0.year + k * rule.interval  # YEARLY
    walls: list[datetime] = []
    for m in rule.bymonth or [day0.month]:
        walls += _month_walls(y, m, rule, d0)
    return datetime(y, 1, 1), walls


def _periods_between(freq: str, a: datetime, b: datetime, interval: int) -> int:
    if freq == "DAILY":
        return (b - a).days // interval
    if freq == "WEEKLY":
        return (b - a).days // (7 * interval)
    if freq == "MONTHLY":
        return ((b.year - a.year) * 12 + b.month - a.month) // interval
    return (b.year - a.year) // interval


def _is_simple(rule: _Rule, d0: datetime) -> bool:
    """Exactly one occurrence per period, so occurrence number k+1 is period k and COUNT can be
    fast-forwarded arithmetically: no BY* filter, and no day 29-31 / 29 Feb that skips periods."""
    if rule.byday or rule.bymonthday or rule.bymonth or rule.bysetpos:
        return False
    if rule.freq == "MONTHLY":
        return d0.day <= 28
    if rule.freq == "YEARLY":
        return not (d0.month == 2 and d0.day == 29)
    return True


def _expand(b: _Base, rule: _Rule, ws: datetime, horizon: datetime, skip: Callable[[datetime], bool],
            budget: list[int], deadline: Optional[_Deadline] = None) -> list[datetime]:
    """Occurrence starts (aware, in b.tz) of a recurring event that overlap [ws, horizon).
    Anything cut off by a cap sets `deadline.truncated`."""
    out: list[datetime] = []
    wall0 = b.wall
    until = None
    if rule.until_raw:
        try:
            u, is_date, _ = _parse_time(rule.until_raw, {}, b.tz)
            until = u + timedelta(days=1) - timedelta(microseconds=1) if is_date else u
        except (ValueError, OverflowError):
            until = None
    ref = ws.astimezone(b.tz).replace(tzinfo=None) - b.dur - timedelta(days=2)
    hz = horizon.astimezone(b.tz).replace(tzinfo=None) + timedelta(days=1)
    k0 = 0
    simple = _is_simple(rule, wall0)
    if (rule.count is None or simple) and ref > wall0:
        k0 = max(0, _periods_between(rule.freq, wall0, ref, rule.interval) - 1)
    produced = k0 if simple else 0  # a simple rule makes one occurrence per skipped period
    for step, k in enumerate(range(k0, k0 + MAX_STEPS)):
        if deadline is not None and step % 64 == 0:
            deadline.check()
        budget[0] -= 1
        if budget[0] < 0:
            if deadline is not None:
                deadline.truncated = True
            break
        try:
            period_start, walls = _period(rule, wall0, k)
        except (ValueError, OverflowError):
            break
        if period_start > hz:
            break
        for w in walls:
            if w < wall0:
                continue
            try:
                start = w.replace(tzinfo=b.tz)
                end = b.end_of(start)
            except (ValueError, OverflowError):
                continue
            if until is not None and start > until:
                return out
            produced += 1
            if rule.count is not None and produced > rule.count:
                return out
            if skip(start):
                continue
            if (end > ws or start >= ws) and start < horizon:
                out.append(start)
                if len(out) >= MAX_PER_EVENT:
                    if deadline is not None:
                        deadline.truncated = True
                    return out
    else:  # MAX_STEPS periods walked without reaching the horizon, UNTIL or COUNT
        if deadline is not None:
            deadline.truncated = True
    return out


# ---------------------------------------------------------------- build the result
def _day_name(d, today) -> str:
    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    return f"{d:%a} {d.day} {d:%b}"


def _when(e: dict, now: datetime) -> str:
    today = now.date()
    start, end = e["start"], e["end"]
    if e["all_day"]:
        last = end.date() - timedelta(days=1)
        if last > start.date():
            return f"Until {_day_name(last, today)}" if start <= now else f"{_day_name(start.date(), today)} - {_day_name(last, today)}"
        return _day_name(start.date(), today)
    if start <= now < end:
        until = f"{end:%H:%M}" if end.date() == today else f"{_day_name(end.date(), today)} {end:%H:%M}"
        return f"Now, until {until}"
    return f"{_day_name(start.date(), today)} {start:%H:%M}"


def _collect(text: str, local: tzinfo, ws: datetime, horizon: datetime, deadline: Optional[_Deadline] = None) -> list[dict]:
    """Every event (expanded) overlapping [ws, horizon), as dicts with aware local datetimes."""
    vevents = _vevents(_unfold(text, deadline), deadline)
    blocked: set[tuple[str, datetime]] = set()  # (uid, instant) replaced by a RECURRENCE-ID override
    for ev in vevents:
        if "RECURRENCE-ID" in ev and "UID" in ev:
            try:
                rid, _, _ = _parse_time(ev["RECURRENCE-ID"][0][1], ev["RECURRENCE-ID"][0][0], local)
                blocked.add((ev["UID"][0][1].strip(), rid.astimezone(timezone.utc)))
            except (ValueError, OverflowError):
                pass
    found: list[dict] = []
    budget = [MAX_TOTAL_STEPS]
    for ev in vevents:
        if len(found) >= MAX_EXPANDED:
            if deadline is not None:
                deadline.truncated = True
            break
        if deadline is not None:
            deadline.check()
        try:
            if str(ev.get("STATUS", [({}, "")])[0][1]).strip().upper() == "CANCELLED":
                continue
            b = _base(ev, local)
            if b is None:
                continue
            uid = ev["UID"][0][1].strip() if "UID" in ev else None
            rule = _parse_rule(ev["RRULE"][0][1]) if "RRULE" in ev and "RECURRENCE-ID" not in ev else None
            if rule is None:
                end = b.end_of(b.start)
                starts = [b.start] if ((end > ws or b.start >= ws) and b.start < horizon) else []
            else:
                ex_instants: set[datetime] = set()
                ex_dates: set = set()
                for params, value in ev.get("EXDATE", []):
                    for v in value.split(","):
                        try:
                            when, is_date, _ = _parse_time(v, params, local)
                        except (ValueError, OverflowError):
                            continue
                        (ex_dates.add(when.date()) if is_date else ex_instants.add(when.astimezone(timezone.utc)))

                def skip(start: datetime, uid=uid, ex_instants=ex_instants, ex_dates=ex_dates) -> bool:
                    instant = start.astimezone(timezone.utc)
                    return instant in ex_instants or start.date() in ex_dates or (uid is not None and (uid, instant) in blocked)

                starts = _expand(b, rule, ws, horizon, skip, budget, deadline)
            title = (_text(ev, "SUMMARY", 200) or "(no title)").replace("\n", " ")
            location = _text(ev, "LOCATION", 200)
            description = _text(ev, "DESCRIPTION", 300)
            for s in starts:
                found.append({"title": title, "start": s.astimezone(local), "end": b.end_of(s).astimezone(local),
                              "all_day": b.all_day, "location": location, "description": description})
                if len(found) >= MAX_EXPANDED:
                    break
        except (ValueError, OverflowError, KeyError, IndexError, TypeError):
            continue  # one bad event never fails the feed
    return found


def build(text: str, local: tzinfo, tzname: str, now: datetime, days_ahead: int, max_events: int,
          deadline: Optional[_Deadline] = None) -> dict:
    deadline = deadline or _Deadline()
    now = now.astimezone(local)
    today = now.date()

    def midnight(offset: int) -> datetime:
        return datetime.combine(today + timedelta(days=offset), time.min, tzinfo=local)

    ws = midnight(0)
    horizon = midnight(max(days_ahead, 7))
    list_end = midnight(days_ahead)
    found = _collect(text, local, ws, horizon, deadline)
    found.sort(key=lambda e: (e["start"], not e["all_day"], e["title"]))

    def overlaps(e: dict, a: datetime, b: datetime) -> bool:
        return e["start"] < b and (e["end"] > a or e["start"] >= a)

    today_n = sum(1 for e in found if overlaps(e, midnight(0), midnight(1)))
    week_n = sum(1 for e in found if overlaps(e, midnight(0), midnight(7)))
    upcoming = [e for e in found if e["start"] < list_end and (e["end"] > now or e["start"] >= now)][:max_events]
    items = []
    for e in upcoming:
        item: dict[str, Any] = {"title": e["title"], "meta": _when(e, now)}
        if e["location"]:
            item["subtitle"] = e["location"]
        if e["start"] <= now < e["end"]:
            item["status"] = "online"
        items.append(item)
    nxt = upcoming[0] if upcoming else None
    result = {
        "timezone": tzname,
        "truncated": deadline.truncated,
        "stats": [
            stat("events_today", "Events today", today_n),
            stat("events_week", "Events this week", week_n),
            stat("next_event", "Next event", nxt["title"] if nxt else None),
            stat("next_time", "Next event time", _when(nxt, now) if nxt else None),
        ],
        "items": items,
        "events": [{"title": e["title"], "start": e["start"].isoformat(timespec="seconds"),
                    "end": e["end"].isoformat(timespec="seconds"), "all_day": e["all_day"],
                    "location": e["location"], "description": e["description"]} for e in upcoming],
    }
    if deadline.truncated:
        result["note"] = "some events were left out: the calendar has more recurring events than the safety limits allow"
    return result


# ---------------------------------------------------------------- fetch
def _feed_url(ctx: Context) -> str:
    raw = str(ctx.cfg.get("url") or "").strip()
    m = re.match(r"^webcals?://", raw, re.I)
    return "https://" + raw[m.end():] if m else ctx.raw_url()


async def _parse(ctx: Context, text: str, local: tzinfo, tzname: str, days: int, limit: int) -> dict:
    """build() on the dedicated parse threads. One parse per integration at a time, and an abandoned
    parse (the fetch timed out or was cancelled) is told to stop, so threads cannot pile up."""
    if ctx.state.get("parsing"):
        raise IntegrationError("calendar too complex (the previous parse is still running)")
    cancel = threading.Event()
    now = _now()

    def work() -> dict:
        try:
            if cancel.is_set():
                raise IntegrationError("calendar too complex")
            return build(text, local, tzname, now, days, limit, _Deadline(PARSE_DEADLINE, cancel))
        finally:
            ctx.state["parsing"] = False

    ctx.state["parsing"] = True
    try:
        return await asyncio.get_running_loop().run_in_executor(_POOL, work)
    except BaseException:
        cancel.set()
        raise


async def fetch(ctx: Context) -> dict:
    url = _feed_url(ctx)
    tzname = str(ctx.cfg.get("timezone") or "UTC").strip() or "UTC"
    local = _zone(tzname)
    user = ctx.cfg.get("username")
    auth = httpx.BasicAuth(user, ctx.cfg.get("password") or "") if user else None
    async with stream_follow(ctx.client, "GET", url, auth=auth, plain_headers={"Accept": "text/calendar, */*"}) as resp:
        if resp.status_code in (401, 403):
            raise AuthFailed("login failed: check the calendar username and password")
        resp.raise_for_status()
        body = await read_capped(resp, MAX_BYTES, message="calendar feed is too large (over 5 MB)")
    text = body.decode("utf-8-sig", errors="replace")
    if "BEGIN:VCALENDAR" not in text[:4096].upper():
        raise IntegrationError("not an iCalendar feed")
    days = int(ctx.cfg.get("days_ahead") or 14)
    limit = int(ctx.cfg.get("max_events") or 20)
    return await _parse(ctx, text, local, tzname, max(days, 1), max(limit, 1))


SPEC = Spec(
    type="ics",
    label="Calendar (ICS)",
    description="Upcoming events from an iCalendar (.ics / webcal) feed, with recurring events expanded.",
    fetch=fetch,
    default_interval=900,
    provides=("stats", "items"),
    secret_url=True,  # private calendar addresses carry the secret in the path
    validate=_validate,
    fields=[
        {"key": "url", "label": "Calendar URL", "type": "url", "required": True, "schemes": ["http", "https", "webcal"],
         "placeholder": "https://calendar.example.com/feed.ics",
         "help": "A webcal:// address is fetched over https."},
        {"key": "username", "label": "Username", "type": "text", "help": "Only for feeds behind basic auth."},
        {"key": "password", "label": "Password", "type": "secret"},
        {"key": "days_ahead", "label": "Days ahead", "type": "number", "default": 14, "min": 1, "max": 366},
        {"key": "max_events", "label": "Max events", "type": "number", "default": 20, "min": 1, "max": 200},
        {"key": "timezone", "label": "Time zone", "type": "text", "default": "UTC", "placeholder": "Europe/London",
         "help": "IANA name used for dates and for floating times."},
    ],
)
