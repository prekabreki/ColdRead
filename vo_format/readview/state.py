"""Shared read state for the read-view library.

One JSON file, one HTTP service, per-field last-write-wins. Runs on whatever
host serves the library — which is a box with nothing installed on it, so this
module imports ONLY the standard library. A test asserts that.

The whole design turns on one distinction: clients send DURATIONS ("this
happened 3 hours ago") and the server assigns TIMESTAMPS. A phone, a desktop
and a server do not agree about what time it is, but a device measuring an
elapsed time against itself is reliable — so ordering survives without anyone
having to trust anyone's clock.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import pathlib
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlsplit

#: A queued write older than this is treated as exactly this old. Bounds the
#: damage a device with a badly wrong clock can do to the ordering.
MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000


def clamp_age(age: object) -> int:
    """Coerce a client-supplied age in ms into something safe to subtract.

    Anything unusable becomes 0, meaning "treat this as happening now". A
    malformed age must never take down the request that carried it: the whole
    point of the queue is that a device gets its writes through eventually.
    """
    try:
        value = int(float(age))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    if value < 0:
        return 0
    return min(value, MAX_AGE_MS)


class Clock:
    """A millisecond clock that cannot go backwards.

    The guard belongs HERE, on the base reading, and not on the finished stamp.
    Stamps are deliberately back-dated by a write's age, so a rule like "never
    issue a stamp below the highest issued" would forbid exactly the back-dating
    that makes a late flush land in its correct place in history. Making the base
    monotonic means an NTP correction on the host cannot reorder anything, while
    ages still work.
    """

    def __init__(self, source: Callable[[], int] | None = None) -> None:
        self._source = source or (lambda: int(time.time() * 1000))
        self._last = 0

    def now(self) -> int:
        value = int(self._source())
        if value <= self._last:
            value = self._last + 1
        self._last = value
        return value


def merge_field(current: dict | None, value: object, t: int) -> dict | None:
    """The new field, or None meaning "keep what is already there".

    Equal timestamps lose, which makes a replayed flush idempotent.
    """
    if current is not None and t <= current.get("t", 0):
        return None
    return {"v": value, "t": t}


def apply_patch(fields: dict, patch: dict, clock: Clock) -> dict:
    """Merge a client patch into `fields` in place, and return `fields`.

    Shape: {namespace: {field: {"v": value, "age_ms": int}}}. Anything that does
    not fit is skipped rather than raised — one bad field must not discard the
    good ones sent alongside it.

    A namespace is created only when a field in it actually merges, so a patch
    made entirely of junk leaves no empty namespace behind to be saved to disk.
    """
    if not isinstance(patch, dict):
        return fields
    for namespace, incoming in patch.items():
        if not isinstance(incoming, dict):
            continue
        existing = fields.get(namespace)
        if existing is not None and not isinstance(existing, dict):
            continue
        target: dict | None = existing
        for name, entry in incoming.items():
            if not isinstance(entry, dict) or "v" not in entry:
                continue
            t = clock.now() - clamp_age(entry.get("age_ms"))
            current = target.get(name) if target is not None else None
            merged = merge_field(current, entry["v"], t)
            if merged is None:
                continue
            if target is None:
                target = {}
                fields[namespace] = target
            target[name] = merged
    return fields


class Store:
    """The state file and the rules for changing it.

    Not internally locked — `Handler` serialises access, because the lock has to
    cover read-modify-write as one unit and only the caller knows where that
    boundary is.
    """

    def __init__(self, path: pathlib.Path, clock: Clock | None = None) -> None:
        self.path = pathlib.Path(path)
        self.clock = clock or Clock()
        self.fields: dict = {}

    def load(self) -> None:
        """Read the file. Any problem yields empty state and a loud stderr line.

        Deliberately tolerant and deliberately not silent. Raising would take the
        whole library down over a preferences file; staying quiet would let a
        page conclude it is synced against state that is not there.
        """
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.fields = {}
            return
        except OSError as exc:
            print(
                f"state: {self.path} unreadable ({exc}); starting empty",
                file=sys.stderr,
            )
            self.fields = {}
            return
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            print(
                f"state: {self.path} is not valid JSON ({exc}); starting empty",
                file=sys.stderr,
            )
            self.fields = {}
            return
        self.fields = parsed if isinstance(parsed, dict) else {}

    def snapshot(self) -> dict:
        return {"now": self.clock.now(), "fields": self.fields}

    def apply(self, patch: dict) -> dict:
        apply_patch(self.fields, patch, self.clock)
        self.save()
        return self.snapshot()

    def save(self) -> None:
        """Write atomically, keeping one rotating backup.

        temp + os.replace, so a crash mid-write leaves the previous file intact
        rather than a truncated one. os.replace is atomic on POSIX and on Windows.
        """
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(self.fields, indent=1), encoding="utf-8")
        if self.path.exists():
            backup = self.path.with_name(self.path.name + ".bak")
            os.replace(self.path, backup)
        os.replace(temp, self.path)


#: Cap on a request body. Real payloads are a few hundred bytes; this stops a
#: bad or hostile client from making the service read until it runs out of
#: memory on a box that also does something more important.
MAX_BODY = 256 * 1024

COOKIE_NAME = "coldread_state"


def _cookie_token(header: str | None) -> str:
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME:
            return value
    return ""


def make_handler(store: Store, token: str, lock: threading.Lock) -> type:
    """A request handler bound to one store.

    The lock is passed in rather than created here because it must cover
    read-modify-write as a single unit, and ThreadingHTTPServer means two
    devices really can POST at the same moment.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "coldread-state"

        def log_message(self, fmt: str, *args: object) -> None:
            # http.server logs the full request line by default, which would
            # write the token into the journal on every single request.
            scrubbed = [
                str(a).split("?")[0] if isinstance(a, str) else a for a in args
            ]
            super().log_message(fmt, *scrubbed)

        # --- guards ---------------------------------------------------------
        def _authorised(self) -> bool:
            query = parse_qs(urlsplit(self.path).query)
            supplied = (query.get("k") or [""])[0]
            from_query = bool(supplied)
            if not supplied:
                supplied = _cookie_token(self.headers.get("Cookie"))
            # Compared as bytes: compare_digest raises TypeError on a non-ASCII
            # str, and a hostile header must earn a 403, not a 500 traceback.
            if not hmac.compare_digest(
                supplied.encode("utf-8"), token.encode("utf-8")
            ):
                return False
            self._set_cookie = from_query
            return True

        def _same_origin(self) -> bool:
            """Reject a PRESENT and foreign origin. Absence is fine.

            A Host check alone would not cover this: the browser derives Host
            from the request target, so it stops DNS rebinding but not a blind
            cross-origin POST.
            """
            site = self.headers.get("Sec-Fetch-Site")
            if site and site not in ("same-origin", "none"):
                return False
            origin = self.headers.get("Origin")
            if origin:
                host = self.headers.get("Host", "")
                return urlsplit(origin).netloc == host
            return True

        def _reject(self, code: int, text: str) -> None:
            payload = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_json(self, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if getattr(self, "_set_cookie", False):
                self.send_header(
                    "Set-Cookie",
                    f"{COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/",
                )
            self.end_headers()
            self.wfile.write(body)

        def _prelude(self) -> bool:
            if urlsplit(self.path).path != "/state":
                self._reject(404, "not found")
                return False
            if not self._authorised():
                self._reject(403, "forbidden")
                return False
            if not self._same_origin():
                self._reject(403, "forbidden")
                return False
            return True

        # --- routes ---------------------------------------------------------
        def do_GET(self) -> None:
            if not self._prelude():
                return
            with lock:
                self._send_json(store.snapshot())

        def do_POST(self) -> None:
            if not self._prelude():
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._reject(400, "bad length")
                return
            if length > MAX_BODY:
                self._reject(413, "too large")
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                parsed = json.loads(raw.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                self._reject(400, "bad json")
                return
            patch = parsed.get("fields") if isinstance(parsed, dict) else None
            with lock:
                self._send_json(store.apply(patch if isinstance(patch, dict) else {}))

        def do_PUT(self) -> None:
            self._reject(405, "method not allowed")

        do_DELETE = do_PUT
        do_PATCH = do_PUT
        do_HEAD = do_PUT
        do_OPTIONS = do_PUT

    return Handler


class _StateServer(ThreadingHTTPServer):
    """`ThreadingHTTPServer` that actually refuses a port already in use.

    `http.server` sets `allow_reuse_address = 1`, which on POSIX only relaxes
    TIME_WAIT — the reason it is wanted, so a restart is not blocked for a
    minute. On Windows SO_REUSEADDR means something else entirely: it lets a
    second process bind a port another process is *actively listening on*. The
    bind then succeeds, two services claim the port, and which one answers is
    undefined — precisely the "appears to start" failure the spec rates as worse
    than dying. Off on Windows, unchanged everywhere else.
    """

    allow_reuse_address = os.name != "nt"


def serve(store: Store, token: str, host: str, port: int) -> None:
    handler = make_handler(store, token, threading.Lock())
    httpd = _StateServer((host, port), handler)
    print(f"state: serving {store.path} on http://{host}:{port}/state")
    httpd.serve_forever()


def load_or_create_token(path: pathlib.Path) -> str:
    """The shared token, generated once and reused.

    256 bits. The host is reachable from wherever its private network reaches,
    so "it is only on my LAN" is not a defence — see the spec.
    """
    path = pathlib.Path(path)
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        return existing
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)  # best effort; a no-op on some filesystems
    except OSError:
        pass
    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coldread-state",
        description="Serve shared read state for a ColdRead read-view library.",
    )
    parser.add_argument("--state-file", required=True, type=pathlib.Path)
    parser.add_argument("--token-file", required=True, type=pathlib.Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration and exit without serving",
    )
    args = parser.parse_args(argv)

    # Loud, named, non-zero. The failure this guards against is a service that
    # starts, answers 200, and quietly discards every write.
    #
    # The directory is NOT created here: a mistyped path is far likelier than a
    # deliberately absent one, and silently conjuring `…/nope/deeper/` would
    # serve state from somewhere nobody is looking. Refusing names the typo.
    parent = args.state_file.parent
    try:
        probe = parent / ".coldread-state-write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        print(
            f"error: cannot write beside {args.state_file} ({exc})",
            file=sys.stderr,
        )
        return 2

    try:
        token = load_or_create_token(args.token_file)
    except OSError as exc:
        print(
            f"error: token file {args.token_file} unusable ({exc})",
            file=sys.stderr,
        )
        return 2

    store = Store(args.state_file)
    store.load()
    if args.check:
        print(
            f"ok    {args.state_file} writable, token loaded, would bind "
            f"{args.host}:{args.port}"
        )
        return 0
    try:
        serve(store, token, args.host, args.port)
    except OSError as exc:
        print(
            f"error: cannot bind {args.host}:{args.port} ({exc})",
            file=sys.stderr,
        )
        return 2
    return 0


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
