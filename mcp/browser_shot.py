"""Headless-browser screenshots of the running Start Page, for the MCP server's screenshot tools.

Drives an installed Chromium-family browser (Chrome, Edge, Brave, Chromium) over the Chrome
DevTools Protocol, through a private pipe to the browser process (`--remote-debugging-pipe`; on
Windows `--remote-debugging-io-pipes` with two inherited handles). No network port is opened, so no other
local process can attach to the browser, and the browser exits by itself when this process goes away
(the pipe closes). No browser is downloaded and no websocket library is needed. Everything here is plain
Python with no `mcp` import, so it is unit-testable.

Security model: the API token (if any) is never put into a URL, a profile on disk, the browser's
environment (it is scrubbed of STARTPAGE_* and other secret-looking variables) or a global "extra
headers" setting. Every request the page makes is paused (CDP Fetch domain) and decided one by one,
per redirect hop (see `request_decision`):

  * the app's own origin: the Authorization header is added only to safe-method requests under /api/ made
    by the top frame; the same request from any other frame (a custom widget runs in a sandboxed frame) is
    refused, and so is every unsafe method;
  * any other origin: only GET for images, fonts, stylesheets and media, sent WITHOUT Authorization or
    Cookie headers (so CDN icons and third-party images render); documents, scripts, XHR/fetch and
    everything else are refused, so another site's page can never load in a frame;
  * WebSockets are not paused by Fetch: they are blocked outright (Network.setBlockedURLs).

Other local-network hosts can therefore receive a credential-free GET for an image, font, stylesheet or
media file the page asks for. The token is only ever sent to the app's origin.
"""

import atexit
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit, urlunsplit

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MAX_FULL_HEIGHT = 8000  # css px; taller pages are cropped
MAX_PNG_BYTES = 1_500_000  # images go to the model: shrink above this
MAX_PIXELS = 36_000_000  # width * height * scale^2 of one capture
MAX_RETAKES = 8
SUBRESOURCE_TYPES = {"Image", "Font", "Stylesheet", "Media"}  # what another origin may supply (GET, no credentials)
READY_TIMEOUT = 20.0  # load + app ready + network quiet
QUIET_WINDOW = 0.5  # seconds without a request in flight
STARTUP_TIMEOUT = 15.0
COMMAND_TIMEOUT = 20.0
LIMITS = {"width": (320, 3840), "height": (320, 4000), "scale": (0.5, 3.0), "wait_ms": (0, 10000), "padding": (0, 200)}
THEMES = ("light", "dark")


class ShotError(Exception):
    """A problem to report to the user as is (the MCP wrapper turns it into a ToolError)."""


# ---------- arguments ----------
def clamp(value, lo, hi):
    return max(lo, min(hi, value))


def clamp_args(width: int, height: int, scale: float, wait_ms: int) -> tuple[int, int, float, int]:
    return (
        int(clamp(int(width), *LIMITS["width"])),
        int(clamp(int(height), *LIMITS["height"])),
        float(clamp(float(scale), *LIMITS["scale"])),
        int(clamp(int(wait_ms), *LIMITS["wait_ms"])),
    )


def check_theme(theme: Optional[str]) -> Optional[str]:
    if theme is None:
        return None
    if theme not in THEMES:
        raise ShotError(f"theme must be 'light' or 'dark' (got {theme!r}); omit it to use the page's own theme.")
    return theme


def public_url(url: str) -> str:
    """The URL without user:password@ (never echoed in an error or sent to the browser)."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))
    except ValueError:
        return "the configured address"


def check_base_url(url: str) -> str:
    """http(s) only, with a host; returns the URL without userinfo and trailing slash."""
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        parts, host = None, None
    if parts is None or parts.scheme not in ("http", "https") or not host:
        raise ShotError(f"STARTPAGE_URL must be an http:// or https:// address (got {public_url(url)!r}); screenshots are not taken for other schemes.")
    return public_url(url).rstrip("/")


def clean_text(text: Any, limit: int = 200) -> str:
    """Page-derived text made safe to show: control characters removed, whitespace collapsed, shortened."""
    text = " ".join(re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", " ", str(text)).split())
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


# ---------- origin rule ----------
def origin_of(url: str) -> Optional[tuple[str, str, int]]:
    """(scheme, host, port) of an http(s) URL, defaults filled in; None for anything else (ws(s) included)."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https") or not parts.hostname:
            return None
        return scheme, parts.hostname.lower(), parts.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None


def request_decision(
    url: str, method: str, app_origin: tuple[str, str, int], resource_type: str = "", frame_is_top: bool = True
) -> str:
    """What to do with one request (or one redirect hop) the page makes:

    "auth"  continue, with the token added   (app origin, safe method, path under /api/, top frame)
    "allow" continue as is                   (data:, about:blank, app blob:, the app's own non-API files)
    "strip" continue with an explicit header list that has no Authorization / Cookie
                                             (another origin: GET, http(s), Image / Font / Stylesheet / Media)
    "block" fail the request                 (everything else)

    | origin   | method | type                      | frame | path    | decision |
    |----------|--------|---------------------------|-------|---------|----------|
    | app      | unsafe | any                       | any   | any     | block    |
    | app      | safe   | any                       | top   | /api/*  | auth     |
    | app      | safe   | any                       | other | /api/*  | block    |
    | app      | safe   | any                       | any   | other   | allow    |
    | other    | GET    | Image/Font/Stylesheet/Media | any | any     | strip    |
    | other    | other  | any                       | any   | any     | block    |
    """
    low = url.lower()
    if low.startswith("data:") or low == "about:blank":
        return "allow"
    if low.startswith("blob:"):
        return "allow" if origin_of(url[5:]) == app_origin else "block"
    origin = origin_of(url)
    if origin is None:
        return "block"
    method = method.upper()
    if origin == app_origin:
        if method not in SAFE_METHODS:
            return "block"
        if urlsplit(url).path.lower().startswith("/api/") or urlsplit(url).path.lower() == "/api":
            return "auth" if frame_is_top else "block"
        return "allow"
    if method == "GET" and resource_type in SUBRESOURCE_TYPES:
        return "strip"
    return "block"


# ---------- finding and launching the browser ----------
def browser_candidates(platform: str, env: dict, which: Callable[[str], Optional[str]]) -> list[str]:
    """Where a Chromium-family browser usually lives on this OS, most preferred first."""
    if platform.startswith("win"):
        roots = [env.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        rels = [
            r"Google\Chrome\Application\chrome.exe",
            r"Microsoft\Edge\Application\msedge.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
            r"Chromium\Application\chrome.exe",
        ]
        return [str(Path(r) / rel) for rel in rels for r in roots if r]
    if platform == "darwin":
        apps = [
            "Google Chrome.app/Contents/MacOS/Google Chrome",
            "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "Brave Browser.app/Contents/MacOS/Brave Browser",
            "Chromium.app/Contents/MacOS/Chromium",
        ]
        home = env.get("HOME")
        return [f"/Applications/{a}" for a in apps] + ([f"{home}/Applications/{a}" for a in apps] if home else [])
    names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "microsoft-edge-stable", "brave-browser", "chrome"]
    found = [p for p in (which(n) for n in names) if p]
    return found + ["/snap/bin/chromium", "/usr/bin/chromium", "/usr/bin/google-chrome"]


NO_BROWSER = (
    "No Chromium-family browser found (looked for Chrome, Edge, Brave and Chromium in the usual places). "
    "Install one, or set STARTPAGE_BROWSER to the browser's executable path in .mcp.json "
    "(under the startpage server's \"env\") and restart Claude Code."
)


def find_browser(env: Optional[dict] = None, platform: Optional[str] = None, exists: Callable[[str], bool] = os.path.isfile, which: Callable[[str], Optional[str]] = shutil.which) -> str:
    env = os.environ if env is None else env
    override = (env.get("STARTPAGE_BROWSER") or "").strip().strip('"')
    if override:
        if not exists(override):
            raise ShotError(f"STARTPAGE_BROWSER is set to {override!r}, but there is no file there. Point it at the browser's executable.")
        return override
    for path in browser_candidates(platform or sys.platform, env, which):
        if exists(path):
            return path
    raise ShotError(NO_BROWSER)


def launch_args(exe: str, profile_dir: str) -> list[str]:
    """Command line for a throwaway headless browser. Nothing here weakens the sandbox or web security.
    The DevTools connection is a pipe (added by `_spawn`), never a TCP port."""
    return [
        exe,
        "--headless=new",
        "--remote-debugging-pipe",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--disable-component-update",
        "--hide-scrollbars",
        "--mute-audio",
        "--force-color-profile=srgb",
        "about:blank",
    ]


_SECRET_ENV = re.compile(r"(token|secret|passw|pwd|api[-_]?key|credential|private[-_]?key|auth)", re.I)


def scrubbed_env(env: Optional[dict] = None) -> dict:
    """The browser's environment: ours without STARTPAGE_* and anything that looks like a secret."""
    env = os.environ if env is None else env
    return {k: v for k, v in env.items() if not k.upper().startswith("STARTPAGE_") and not _SECRET_ENV.search(k)}


def kill_tree(proc) -> None:
    """Kill the browser and everything it spawned."""
    try:
        if proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
            else:
                import signal

                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001 - cleanup must never raise over the real error
        pass


_LIVE: list["Browser"] = []


def _cleanup_all() -> None:
    for b in list(_LIVE):
        b.close()


atexit.register(_cleanup_all)
PROFILE_PREFIX = "startpage-shot-"


def sweep_stale_profiles(max_age: float = 120.0) -> None:
    """Remove profiles left by a capture whose process was killed (the browser itself exits when its pipe closes)."""
    try:
        root = Path(tempfile.gettempdir())
        for d in root.glob(PROFILE_PREFIX + "*"):
            try:
                if d.is_dir() and time.time() - d.stat().st_mtime > max_age:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


class PipeTransport:
    """CDP over the browser's pipe pair: UTF-8 JSON messages, each terminated by a NUL byte.
    A reader thread feeds a queue, so recv(timeout) works the same on every OS."""

    def __init__(self, read_fd: int, write_fd: int):
        self.read_fd, self.write_fd = read_fd, write_fd
        self._q: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._closed = False
        self._wlock = threading.Lock()
        threading.Thread(target=self._reader, daemon=True, name="cdp-pipe-reader").start()

    def _reader(self) -> None:
        buf = b""
        try:
            while True:
                chunk = os.read(self.read_fd, 1 << 20)
                if not chunk:
                    break
                buf += chunk
                while (i := buf.find(b"\0")) >= 0:
                    self._q.put(buf[:i])
                    buf = buf[i + 1 :]
        except OSError:
            pass
        finally:
            self._q.put(None)

    def send(self, text: str) -> None:
        with self._wlock:
            data = text.encode("utf-8") + b"\0"
            while data:
                n = os.write(self.write_fd, data)
                data = data[n:]

    def recv(self, timeout: float) -> str:
        try:
            item = self._q.get(timeout=max(0.0, timeout))
        except queue.Empty:
            raise TimeoutError from None
        if item is None:
            self._q.put(None)
            raise ConnectionError("the browser closed the pipe")
        return item.decode("utf-8", errors="replace")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for fd in (self.write_fd, self.read_fd):
            try:
                os.close(fd)
            except OSError:
                pass


class Browser:
    """A launched browser, its DevTools pipe and its temporary profile. close() is idempotent and always safe."""

    def __init__(self, proc, profile_dir: str, transport=None):
        self.proc, self.profile_dir, self.transport, self.closed = proc, profile_dir, transport, False
        _LIVE.append(self)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.transport is not None:
            self.transport.close()  # the browser exits when its pipe closes; the kill below is the backstop
        kill_tree(self.proc)
        for _ in range(10):  # Windows keeps profile files locked for a moment after the kill
            shutil.rmtree(self.profile_dir, ignore_errors=True)
            if not os.path.exists(self.profile_dir):
                break
            time.sleep(0.2)
        if self in _LIVE:
            _LIVE.remove(self)


def abort_all() -> None:
    """Kill every browser this process started (a cancelled capture: its worker thread then fails fast and cleans up)."""
    _cleanup_all()


def _spawn(args: list[str], popen: Callable, env: dict):
    """Start the browser with a private pipe pair. Returns (proc, transport or None)."""
    base: dict[str, Any] = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    if os.name == "nt":
        import msvcrt

        in_r, in_w = os.pipe()  # the browser reads in_r, we write in_w
        out_r, out_w = os.pipe()  # the browser writes out_w, we read out_r
        h_in, h_out = msvcrt.get_osfhandle(in_r), msvcrt.get_osfhandle(out_w)
        os.set_handle_inheritable(h_in, True)
        os.set_handle_inheritable(h_out, True)
        info = subprocess.STARTUPINFO()
        info.lpAttributeList = {"handle_list": [h_in, h_out]}  # only these two handles are inherited
        try:
            proc = popen(args + [f"--remote-debugging-io-pipes={h_in},{h_out}"], close_fds=True, startupinfo=info, **base)
        except BaseException:
            for fd in (in_r, in_w, out_r, out_w):
                os.close(fd)
            raise
        os.close(in_r)  # the browser's ends now belong to the browser
        os.close(out_w)
        return proc, PipeTransport(out_r, in_w)

    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()

    def child() -> None:  # the browser expects its DevTools pipe on fds 3 (read) and 4 (write)
        os.dup2(in_r, 3)
        os.dup2(out_w, 4)
        os.set_inheritable(3, True)
        os.set_inheritable(4, True)

    try:
        proc = popen(args, preexec_fn=child, start_new_session=True, **base)
    except BaseException:
        for fd in (in_r, in_w, out_r, out_w):
            os.close(fd)
        raise
    os.close(in_r)
    os.close(out_w)
    return proc, PipeTransport(out_r, in_w)


def launch(exe: str, *, popen: Callable = subprocess.Popen, startup_timeout: float = STARTUP_TIMEOUT) -> Browser:
    """Start the browser (scrubbed environment, private DevTools pipe) and wait until it answers."""
    sweep_stale_profiles()
    profile = tempfile.mkdtemp(prefix=PROFILE_PREFIX)
    try:
        proc, transport = _spawn(launch_args(exe, profile), popen, scrubbed_env())
    except OSError as e:
        shutil.rmtree(profile, ignore_errors=True)
        raise ShotError(f"Could not start the browser {exe!r}: {e}") from e
    browser = Browser(proc, profile, transport)
    try:
        deadline = time.monotonic() + startup_timeout
        try:
            transport.send(json.dumps({"id": 0, "method": "Browser.getVersion"}))  # the first answer proves the pipe works
        except OSError:
            pass  # the browser is already gone; the loop below reports that
        while True:
            try:
                transport.recv(0.1)
                return browser
            except TimeoutError:
                pass
            except ConnectionError:
                pass
            if proc.poll() is not None or time.monotonic() > deadline:
                hint = ""
                if hasattr(os, "geteuid") and os.geteuid() == 0:
                    hint = " Chromium refuses to start as root without --no-sandbox, which this tool deliberately does not add: run Claude Code as a normal user."
                what = "exited during startup" if proc.poll() is not None else f"did not answer on its DevTools pipe within {startup_timeout:.0f} s"
                raise ShotError(f"The browser {what} ({exe}).{hint}")
    except BaseException:
        browser.close()
        raise


# ---------- DevTools protocol client ----------
class CommandError(ShotError):
    """The browser answered a command with an error."""


class CDP:
    """Minimal synchronous CDP client over one transport (flattened sessions).

    Events are dispatched while waiting, from one thread. Fetch.requestPaused events are queued
    and answered after the current message, so the answer never recurses into another handler."""

    def __init__(self, transport):
        self.transport = transport
        self._id = 0
        self._results: dict[int, dict] = {}
        self.handlers: dict[str, list[Callable]] = {}
        self.on_paused: Optional[Callable[..., None]] = None
        self._paused: deque = deque()
        self._draining = False
        self._ignore: set[int] = set()  # ids of post()ed commands: their answers are not kept

    def on(self, method: str, fn: Callable[..., None]) -> None:
        self.handlers.setdefault(method, []).append(fn)

    def pump(self, timeout: float) -> bool:
        """Handle at most one incoming message; False when nothing arrived within `timeout`."""
        try:
            raw = self.transport.recv(timeout)
        except TimeoutError:
            return False
        except Exception as e:  # noqa: BLE001 - the pipe closed, or the browser died
            raise ShotError(f"Lost the connection to the browser ({e.__class__.__name__}).") from e
        msg = json.loads(raw)
        if "id" in msg:
            if msg["id"] in self._ignore:
                self._ignore.discard(msg["id"])
            else:
                self._results[msg["id"]] = msg
        elif msg.get("method") == "Fetch.requestPaused":
            self._paused.append(msg)
        else:
            for fn in self.handlers.get(msg.get("method", ""), []):
                fn(msg.get("params", {}), msg.get("sessionId", ""))
        self._drain()
        return True

    def _drain(self) -> None:
        if self._draining:
            return
        self._draining = True
        try:
            while self._paused:
                msg = self._paused.popleft()
                if self.on_paused:
                    self.on_paused(msg["params"], msg.get("sessionId", ""))
        finally:
            self._draining = False

    def send(self, method: str, params: Optional[dict] = None, session: Optional[str] = None, timeout: float = COMMAND_TIMEOUT) -> dict:
        self._id += 1
        mid = self._id
        msg: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        try:
            self.transport.send(json.dumps(msg))
        except OSError as e:
            raise ShotError(f"Lost the connection to the browser ({e.__class__.__name__}).") from e
        deadline = time.monotonic() + timeout
        while mid not in self._results:
            if time.monotonic() > deadline:
                raise ShotError(f"The browser did not answer {method} within {timeout:.0f} s.")
            self.pump(0.2)
        res = self._results.pop(mid)
        if "error" in res:
            raise CommandError(f"Browser command {method} failed: {res['error'].get('message', res['error'])}")
        return res.get("result", {})

    def post(self, method: str, params: Optional[dict] = None, session: Optional[str] = None) -> None:
        """Send a command without waiting for its answer (used from inside event handlers, where waiting
        would re-enter the message loop). The answer, or its error, is dropped."""
        self._id += 1
        msg: dict[str, Any] = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        try:
            self.transport.send(json.dumps(msg))
        except OSError as e:
            raise ShotError(f"Lost the connection to the browser ({e.__class__.__name__}).") from e
        self._ignore.add(self._id)

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            self.pump(min(left, 0.1))

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception:  # noqa: BLE001
            pass


# ---------- the screenshot itself ----------
@dataclass
class Options:
    base_url: str
    token: str = ""
    page_id: Optional[str] = None
    width: int = 1440
    height: int = 900
    full_page: bool = True
    theme: Optional[str] = None
    wait_ms: int = 1500
    scale: float = 1.0
    widget_id: Optional[str] = None
    padding: int = 8
    browser: Optional[str] = None


@dataclass
class Result:
    png: bytes
    viewport: str = ""
    page_name: str = ""
    state: str = ""  # "page" | "login" | "" (not ready)
    notes: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)


def retry_scale(scale: float, size: int, cap: int = MAX_PNG_BYTES) -> Optional[float]:
    """A smaller scale to retry with when the PNG is over the cap; None when no retry makes sense."""
    if size <= cap or scale <= 0.25:
        return None
    return max(0.25, round(scale * min(0.75, 0.9 * (cap / size) ** 0.5), 2))


def cap_scale(width: int, height: int, scale: float, max_pixels: int = MAX_PIXELS) -> float:
    """`scale`, lowered so that width * height * scale^2 stays within max_pixels (floor 0.25: ShotError below that)."""
    if width * height * scale * scale <= max_pixels:
        return scale
    lowered = math.floor(math.sqrt(max_pixels / (width * height)) * 100) / 100
    if lowered < 0.25:
        raise ShotError(f"A {width}x{height} px capture is too large to render; use a smaller width or full_page=false.")
    return lowered


def init_script(app_origin: str, page_id: Optional[str], theme: Optional[str]) -> str:
    """Runs before the app's own code in every document, but only acts on the app's origin. The
    profile is temporary, so selecting the page and forcing the theme here is never persisted."""
    return (
        "(()=>{try{if(location.origin!==%s)return;"
        "if(%s)localStorage.setItem('sp.dashboard',%s);"
        "const t=%s;if(t){const f=()=>{const r=document.documentElement;if(r&&r.dataset.theme!==t)r.dataset.theme=t};"
        "const go=()=>{f();new MutationObserver(f).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']})};"
        "document.documentElement?go():document.addEventListener('DOMContentLoaded',go)}}catch(e){}})();"
    ) % (json.dumps(app_origin), json.dumps(bool(page_id)), json.dumps(page_id or ""), json.dumps(theme))


PROBE = """(()=>{const d=document,r=d.documentElement,b=d.body;return JSON.stringify({
ready:r.dataset.ready||'',page:(d.querySelector('.tab.active')||{}).textContent||'',
w:innerWidth,h:innerHeight,doc:Math.max(r.scrollHeight,b?b.scrollHeight:0)})})()"""


def widget_rect_js(widget_id: str) -> str:
    return (
        "(()=>{const e=document.querySelector('[data-widget-id='+JSON.stringify(%s)+']');if(!e)return 'null';"
        "const r=e.getBoundingClientRect();"
        "return JSON.stringify({x:r.left+scrollX,y:r.top+scrollY,w:r.width,h:r.height})})()" % json.dumps(widget_id)
    )


class Session:
    """One page in the browser, with the request rule, console capture and readiness waits."""

    def __init__(self, cdp: CDP, opts: Options):
        self.cdp, self.opts = cdp, opts
        self.origin = origin_of(opts.base_url)
        scheme, host, port = self.origin
        host = f"[{host}]" if ":" in host else host
        self.app_origin = f"{scheme}://{host}" + ("" if port == (443 if scheme == "https" else 80) else f":{port}")  # as location.origin spells it
        self.sid = ""
        self.inflight: set[str] = set()
        self.last_busy = time.monotonic()
        self.loaded = False
        self.blocked: list[str] = []
        self.console: list[str] = []
        self.top_frame = ""
        self.target_id = ""
        self.extra_sessions: set[str] = set()
        cdp.on_paused = self._paused
        cdp.on("Network.requestWillBeSent", self._sent)
        cdp.on("Network.loadingFinished", self._done)
        cdp.on("Network.loadingFailed", self._done)
        cdp.on("Page.frameNavigated", self._frame_navigated)
        cdp.on("Target.attachedToTarget", self._attached)
        cdp.on("Page.loadEventFired", lambda p, s="": setattr(self, "loaded", True))
        cdp.on("Runtime.exceptionThrown", self._exception)
        cdp.on("Runtime.consoleAPICalled", self._console_api)
        cdp.on("Log.entryAdded", self._log)

    # events
    def _answer(self, method: str, params: dict, session: str) -> None:
        """Fetch.continueRequest / failRequest. A request the page has already cancelled answers "Invalid
        InterceptionId"; that must not abort the capture."""
        try:
            self.cdp.send(method, params, session)
        except CommandError:
            pass

    def _paused(self, p: dict, session: str = "") -> None:
        req = p["request"]
        method = req.get("method", "GET")
        session = session or self.sid
        top = session == self.sid and bool(self.top_frame) and p.get("frameId") == self.top_frame
        action = request_decision(req["url"], method, self.origin, p.get("resourceType", ""), top)
        rid = p["requestId"]
        if action == "block":
            host = clean_text(urlsplit(req["url"]).netloc or req["url"][:40], 60)
            line = f"{method} {host}"
            if line not in self.blocked:
                self.blocked.append(line)
            self._answer("Fetch.failRequest", {"requestId": rid, "errorReason": "BlockedByClient"}, session)
        elif action == "auth" and self.opts.token:
            headers = [{"name": k, "value": v} for k, v in req.get("headers", {}).items() if k.lower() != "authorization"]
            headers.append({"name": "Authorization", "value": f"Bearer {self.opts.token}"})
            self._answer("Fetch.continueRequest", {"requestId": rid, "headers": headers}, session)
        elif action == "strip":
            # Another origin: an explicit header list, so nothing credential-like can ride along.
            headers = [{"name": k, "value": v} for k, v in req.get("headers", {}).items()
                       if k.lower() not in ("authorization", "cookie", "proxy-authorization")]
            self._answer("Fetch.continueRequest", {"requestId": rid, "headers": headers}, session)
        else:
            self._answer("Fetch.continueRequest", {"requestId": rid}, session)

    def _frame_navigated(self, p: dict, session: str = "") -> None:
        frame = p.get("frame", {})
        if not frame.get("parentId") and session in ("", self.sid):
            self.top_frame = frame.get("id", self.top_frame)

    def _attached(self, p: dict, session: str = "") -> None:
        """A cross-origin iframe becomes its own (out-of-process) target whose requests the page session's
        Fetch does not see. Give it the same interception and rules before it may run (it is started
        paused); its documents are refused anyway, this covers anything that slips through."""
        info = p.get("targetInfo", {})
        sid = p.get("sessionId", "")
        if not sid or info.get("targetId") == self.target_id or info.get("type") == "page":
            return  # our own page's attach event
        self.extra_sessions.add(sid)
        # Posted, not awaited: waiting inside an event handler can stall, and the target is resumed last.
        self.cdp.post("Network.enable", {}, sid)
        self.cdp.post("Network.setBlockedURLs", {"urls": ["ws://*", "wss://*"]}, sid)
        self.cdp.post("Network.setBypassServiceWorker", {"bypass": True}, sid)
        self.cdp.post("Fetch.enable", {"patterns": [{"urlPattern": "*"}]}, sid)
        self.cdp.post("Runtime.runIfWaitingForDebugger", {}, sid)

    def _sent(self, p: dict, session: str = "") -> None:
        if p.get("type") not in ("EventSource", "WebSocket"):
            self.inflight.add(p["requestId"])

    def _done(self, p: dict, session: str = "") -> None:
        self.inflight.discard(p["requestId"])

    def _note(self, text: str) -> None:
        text = clean_text(text, 200)
        if text and text not in self.console and len(self.console) < 10:
            self.console.append(text)

    def _exception(self, p: dict, session: str = "") -> None:
        d = p.get("exceptionDetails", {})
        self._note("uncaught: " + str((d.get("exception") or {}).get("description") or d.get("text", "")))

    def _console_api(self, p: dict, session: str = "") -> None:
        if p.get("type") == "error":
            self._note("console.error: " + " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args", [])))

    def _log(self, p: dict, session: str = "") -> None:
        e = p.get("entry", {})
        if e.get("level") == "error" and "ERR_BLOCKED_BY_CLIENT" not in e.get("text", ""):
            self._note(f"{e.get('text', '')} {e.get('url', '')}".strip())

    # commands on the page session
    def call(self, method: str, params: Optional[dict] = None, timeout: float = COMMAND_TIMEOUT) -> dict:
        return self.cdp.send(method, params, self.sid, timeout)

    def evaluate(self, js: str) -> Any:  # a CDP Runtime.evaluate inside the page, not Python eval
        res = self.call("Runtime.evaluate", {"expression": js, "returnByValue": True})
        if "exceptionDetails" in res:
            raise ShotError("A script failed inside the page: " + str(res["exceptionDetails"].get("text", "")))
        return res.get("result", {}).get("value")

    def probe(self) -> dict:
        return json.loads(self.evaluate(PROBE))

    def open(self) -> None:
        o = self.opts
        target = self.target_id = self.cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.sid = self.cdp.send("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        for domain in ("Page", "Network", "Runtime", "Log"):
            self.call(f"{domain}.enable")
        self.top_frame = self.call("Page.getFrameTree").get("frameTree", {}).get("frame", {}).get("id", "")
        # Cross-origin iframes run out of process: attach to them (paused) so the same rules reach them.
        self.call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True})
        # WebSockets are not paused by Fetch, so a page script could open one: block them outright.
        self.call("Network.setBlockedURLs", {"urls": ["ws://*", "wss://*"]})
        self.call("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
        self.call("Network.setBypassServiceWorker", {"bypass": True})  # a service worker's fetches would skip our rule
        self.call("Emulation.setDeviceMetricsOverride", {"width": o.width, "height": o.height, "deviceScaleFactor": o.scale, "mobile": False})
        if o.theme:
            self.call("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": o.theme}]})
        self.call("Page.addScriptToEvaluateOnNewDocument", {"source": init_script(self.app_origin, o.page_id, o.theme)})

    def wait_ready(self) -> list[str]:
        """Load, then the app's data-ready marker, then a quiet network, all inside READY_TIMEOUT."""
        notes: list[str] = []
        self.call("Page.navigate", {"url": self.opts.base_url + "/"})
        deadline = time.monotonic() + READY_TIMEOUT
        while not self.loaded and time.monotonic() < deadline:
            self.cdp.pump(0.1)
        if not self.loaded:
            notes.append("Timed out waiting for the page to load; the image may show a partial page.")
        state = ""
        while time.monotonic() < deadline:
            state = self.probe()["ready"]
            if state:
                break
            self.cdp.pump(0.1)
        else:
            notes.append("Timed out waiting for the dashboard to render its data (no ready marker); the image may show a loading state.")
        self.last_busy = time.monotonic()
        while time.monotonic() < deadline:
            self.cdp.pump(0.05)
            if self.inflight:
                self.last_busy = time.monotonic()
            elif time.monotonic() - self.last_busy >= QUIET_WINDOW:
                break
        else:
            notes.append("The network never went quiet before the timeout (something keeps loading); the image may be mid-update.")
        self.cdp.wait(self.opts.wait_ms / 1000)
        return notes

    def resize(self, height: int) -> None:
        o = self.opts
        self.call("Emulation.setDeviceMetricsOverride", {"width": o.width, "height": height, "deviceScaleFactor": o.scale, "mobile": False})
        self.cdp.wait(0.3)

    def shoot(self, extra: Optional[dict] = None) -> bytes:
        import base64

        params = {"format": "png", "fromSurface": True, **(extra or {})}
        return base64.b64decode(self.call("Page.captureScreenshot", params)["data"])


def run_capture(opts: Options, *, find: Callable[[], str] = find_browser, launcher: Callable = launch, cdp_cls: Callable = CDP) -> Result:
    """Take the screenshot described by `opts`. The browser is always killed and its profile removed."""
    exe = opts.browser or find()
    browser = launcher(exe)
    cdp = None
    try:
        cdp = cdp_cls(browser.transport)
        s = Session(cdp, opts)
        s.open()
        notes = s.wait_ready()
        info = s.probe()
        result = Result(png=b"", state=info["ready"], page_name=clean_text(info["page"], 80), notes=notes)
        height = opts.height
        doc = info["doc"]
        if (opts.full_page or opts.widget_id) and doc > height:
            height = min(doc, MAX_FULL_HEIGHT)
            s.resize(height)  # a taller window, so the whole page is on screen (no scrolling, no off-screen capture)
            doc = s.probe()["doc"]
            if min(doc, MAX_FULL_HEIGHT) > height:
                height = min(doc, MAX_FULL_HEIGHT)
                s.resize(height)
            if doc > MAX_FULL_HEIGHT:
                result.notes.append(f"Page is {doc} px tall; cropped to the first {MAX_FULL_HEIGHT} px.")
        extra: dict = {}
        if opts.widget_id:
            rect = json.loads(s.evaluate(widget_rect_js(opts.widget_id)))
            if rect is None:
                raise ShotError(f"Widget {opts.widget_id!r} is not on screen: it is on another page, hidden at width {opts.width}, or the page did not render.")
            pad = opts.padding
            x, y = max(0, rect["x"] - pad), max(0, rect["y"] - pad)
            extra = {"clip": {"x": x, "y": y, "width": max(1, min(rect["w"] + 2 * pad, opts.width - x)), "height": max(1, min(rect["h"] + 2 * pad, height - y)), "scale": 1}}
            if rect["y"] > height:
                result.notes.append("The widget lies below the 8000 px crop, so the image is empty there.")
            result.viewport = f"widget {extra['clip']['width']:.0f}x{extra['clip']['height']:.0f} px at x={x:.0f}, y={y:.0f}"
        else:
            result.viewport = f"{info['w']}x{height} px"
        scale = cap_scale(opts.width, height, opts.scale)
        if scale != opts.scale:
            result.notes.append(f"The scale was lowered from {opts.scale:g} to {scale:g} to keep the capture under {MAX_PIXELS // 1_000_000} megapixels.")
            s.call("Emulation.setDeviceMetricsOverride", {"width": opts.width, "height": height, "deviceScaleFactor": scale, "mobile": False})
        png = s.shoot(extra)
        result.viewport += f" at scale {scale:g}"
        for _ in range(MAX_RETAKES):  # smaller and smaller until the image is light enough for the model
            smaller = retry_scale(scale, len(png))
            if not smaller:
                break
            scale = smaller
            s.call("Emulation.setDeviceMetricsOverride", {"width": opts.width, "height": height, "deviceScaleFactor": scale, "mobile": False})
            png = s.shoot(extra)
            result.notes.append(f"The PNG was over {MAX_PNG_BYTES // 1000} KB, so it was retaken at scale {scale:g}.")
        if len(png) > MAX_PNG_BYTES:
            raise ShotError(
                f"The image is still {len(png) // 1000} KB at the smallest scale (limit {MAX_PNG_BYTES // 1000} KB); "
                "use a smaller width or full_page=false for a lighter image."
            )
        result.png = png
        result.console = [clean_text(c, 200) for c in s.console[:10]]
        if s.blocked:
            shown = "; ".join(s.blocked[:5]) + (f" (+{len(s.blocked) - 5} more)" if len(s.blocked) > 5 else "")
            result.notes.append(
                f"Blocked {len(s.blocked)} request(s) (only images, fonts, stylesheets and media may come from other sites, "
                f"without credentials; the app is reached read-only): {shown}."
            )
        return result
    finally:
        if cdp is not None:
            cdp.close()
        browser.close()
