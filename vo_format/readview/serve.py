"""`coldread-serve` — serve the ColdRead read-view library over HTTP.

Stdlib only. Designed to run on the always-on Pi box, which has nothing
installed on it. Copied there with `scp` rather than `pip install coldread`,
because that would drag reportlab, anthropic, and customtkinter onto a
416MB headless box that also runs the house's DNS.
"""

from __future__ import annotations

import argparse
import datetime
import hmac
import html
import json
import os
import pathlib
import secrets
import socket
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Sequence
from urllib.parse import parse_qs, unquote, urlsplit

_CHANNELS_CONFIG = "channels.json"
_READVIEW_SUFFIX = " - readview.html"
_COOKIE_NAME = "coldread_serve"


# ── host detection ──────────────────────────────────────────────────────────


def _detect_hosts() -> frozenset[str]:
    """Hostnames and IPs the server may legitimately answer on.

    The Pi has three legitimate names: ``raspberrypi.local``, the LAN IP,
    and the tailnet IP.  All three are detected here.  ``--host-allow`` on
    the CLI appends extra entries.  ``127.0.0.1`` and ``localhost`` are
    always included so a test fixture and a local proxy both work.
    """
    hosts: set[str] = {"127.0.0.1", "localhost"}
    hostname = socket.gethostname()
    if hostname:
        hosts.add(hostname)
        hosts.add(f"{hostname}.local")
    try:
        fqdn = socket.getfqdn()
        if fqdn and fqdn != hostname:
            hosts.add(fqdn)
    except OSError:
        pass
    try:
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
            ip = sockaddr[0]
            hosts.add(ip)
    except OSError:
        pass
    return frozenset(hosts)


# ── token ───────────────────────────────────────────────────────────────────


def load_or_create_token(path: pathlib.Path) -> str:
    """The shared token, generated once and reused.

    256 bits, urlsafe-base64.  ``hmac.compare_digest`` is used for every
    comparison — a timing oracle on a token is real even on a LAN.
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
        path.chmod(0o600)
    except OSError:
        pass
    return token


# ── channel config ──────────────────────────────────────────────────────────


def read_channel_config(
    directory: pathlib.Path,
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Read ``channels.json`` from *directory*: display order, display labels.

    Returns ``((), {})`` for anything unusable — absent, unreadable, not
    JSON, or JSON that is not an object.  It never raises; a hand-edited
    file with one stray brace must cost a nicer heading, never the whole
    index.
    """
    try:
        raw = (directory / _CHANNELS_CONFIG).read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError):
        return (), {}
    if not isinstance(parsed, dict):
        return (), {}
    order = parsed.get("order")
    labels = parsed.get("labels")
    return (
        tuple(str(c) for c in order) if isinstance(order, list) else (),
        {str(k): str(v) for k, v in labels.items()}
        if isinstance(labels, dict)
        else {},
    )


# ── library enumeration ─────────────────────────────────────────────────────


def _entry_for(html_path: pathlib.Path, channel: str) -> tuple[str, str, str]:
    """Describe one read-view file.

    Returns ``(filename, title, date)``.  The title is the stem with the
    read-view suffix stripped; the date is the file's mtime in ISO format.
    """
    filename = html_path.name
    stem = html_path.stem
    if stem.endswith(_READVIEW_SUFFIX):
        title = stem[: -len(_READVIEW_SUFFIX)]
    else:
        title = stem
    when = datetime.date.fromtimestamp(html_path.stat().st_mtime).isoformat()
    return filename, title, when


def _grouped(
    entries: Sequence[tuple[str, str, str, str]], order: tuple[str, ...]
) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """Group *(channel, filename, title, date)* by channel, in *order*."""
    by_channel: dict[str, list[tuple[str, str, str]]] = {}
    for channel, filename, title, date in entries:
        by_channel.setdefault(channel, []).append((filename, title, date))

    def rank(ch: str) -> tuple[int, str]:
        if ch in order:
            return (order.index(ch), "")
        return (len(order), ch)

    return [
        (channel, sorted(by_channel[channel], key=lambda e: e[1].lower()))
        for channel in sorted(by_channel, key=rank)
    ]


def build_library(
    root: pathlib.Path,
) -> tuple[dict[str, pathlib.Path], str, int]:
    """Walk *root* one level deep and build the serving data structures.

    Returns ``(files, index_html, count)`` where *files* is the slug→path
    allowlist, *index_html* is the pre-rendered index page, and *count* is
    the number of scripts found.  Raises ``FileNotFoundError`` when *root*
    does not exist and ``ValueError`` when no scripts are found (the server
    must never serve an empty index).
    """
    if not root.is_dir():
        raise FileNotFoundError(f"{root}: library directory not found")
    if not os.access(root, os.R_OK):
        raise PermissionError(f"{root}: library directory is not readable")

    order, labels = read_channel_config(root)

    files: dict[str, pathlib.Path] = {}
    all_entries: list[tuple[str, str, str, str]] = []

    # One level deep only — immediate subdirectories are channels.
    for channel_dir in sorted(root.iterdir()):
        if not channel_dir.is_dir() or channel_dir.name.startswith("."):
            continue
        channel = channel_dir.name
        for html_file in sorted(channel_dir.glob("*.html")):
            if html_file.name == "index.html":
                continue
            if html_file.is_symlink():
                continue
            try:
                resolved = html_file.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not resolved.is_file():
                continue
            filename, title, date = _entry_for(html_file, channel)
            slug = f"{channel}/{filename}"
            files[slug] = html_file
            all_entries.append((channel, filename, title, date))

    if not files:
        raise ValueError(
            f"{root}: no scripts found — refusing to serve an empty index"
        )

    grouped = _grouped(all_entries, order)
    index_html = _render_index(grouped, labels, sum(1 for _ in all_entries))
    return files, index_html, len(files)


# ── index rendering ─────────────────────────────────────────────────────────


def _render_index(
    grouped: list[tuple[str, list[tuple[str, str, str]]]],
    labels: dict[str, str],
    total: int,
) -> str:
    sections: list[str] = []
    for channel, entries in grouped:
        label = html.escape(labels.get(channel, channel))
        noun = "script" if len(entries) == 1 else "scripts"
        rows: list[str] = []
        for filename, title, date in entries:
            href = html.escape(f"{channel}/{filename}")
            safe_title = html.escape(title)
            safe_date = html.escape(date)
            rows.append(
                f'<li><a href="{href}">'
                f"<b>{safe_title}</b>"
                f"<span>{safe_date}</span></a></li>"
            )
        sections.append(
            f'<details open data-channel="{html.escape(channel)}">'
            f"<summary>{label}<span>{len(entries)} {noun}</span></summary>\n"
            f"<ul>\n" + "\n".join(rows) + "\n</ul></details>"
        )
    noun = "script" if total == 1 else "scripts"
    return (
        '<!doctype html>\n<html lang="en"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>ColdRead &mdash; {total} {noun}</title>\n"
        "<style>"
        + _INDEX_CSS
        + "</style>\n</head>\n<body>\n<header>"
        f"<h1>ColdRead &mdash; {total} {noun}</h1>"
        "</header>\n"
        + "\n".join(sections)
        + "\n</body></html>\n"
    )


_INDEX_CSS = """
body{margin:0;padding:24px;background:#121212;color:#e8e6e3;
font-family:"Courier New",monospace;-webkit-text-size-adjust:100%}
header{margin:0 0 18px}
h1{font-size:20px;opacity:.6;font-weight:400;margin:0}
details{margin:0 0 14px;background:#1a1a1a;border-radius:10px;overflow:hidden}
summary{display:flex;align-items:center;gap:10px;padding:16px 18px;
font-size:17px;cursor:pointer;list-style:none;min-height:44px;
-webkit-tap-highlight-color:transparent}
summary::-webkit-details-marker{display:none}
summary::after{content:"\\25b8";opacity:.45;transition:transform .15s}
details[open] summary::after{transform:rotate(90deg)}
summary span{margin-left:auto;opacity:.45;font-size:14px}
ul{list-style:none;padding:0 10px 10px;margin:0}
li{margin:0 0 8px;border-radius:10px;overflow:hidden;background:#1c1c1c}
li a{display:block;padding:16px 18px;color:#e8e6e3;text-decoration:none;
font-size:16px;line-height:1.4;-webkit-tap-highlight-color:transparent}
li a:active{background:#2a2a2a}
b{display:block;font-weight:700;overflow-wrap:anywhere}
li a span{opacity:.45;font-size:13px}
"""


# ── request handler ─────────────────────────────────────────────────────────


def make_handler(
    files: dict[str, pathlib.Path],
    token: str,
    hosts: frozenset[str],
    index_html: str,
) -> type:
    """A request handler bound to one library snapshot."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "coldread-serve"

        def log_message(self, fmt: str, *args: object) -> None:
            scrubbed = [
                str(a).split("?")[0] if isinstance(a, str) else a for a in args
            ]
            super().log_message(fmt, *scrubbed)

        # --- auth -----------------------------------------------------------

        def _authorised(self) -> bool:
            query_parts = urlsplit(self.path).query
            # parse_qs with a bare string works across Python versions.
            supplied = ""
            if query_parts:
                parsed = parse_qs(query_parts)
                supplied = (parsed.get("k") or [""])[0]
            from_query = bool(supplied)
            if not supplied:
                supplied = _cookie_value(self.headers.get("Cookie") or "")
            if not hmac.compare_digest(
                supplied.encode("utf-8"), token.encode("utf-8")
            ):
                return False
            self._set_cookie = from_query
            return True

        # --- security checks ------------------------------------------------

        def _host_allowed(self) -> bool:
            host_raw = self.headers.get("Host", "")
            host = host_raw.rsplit(":", 1)[0]
            return host in hosts

        def _same_origin(self) -> bool:
            site = self.headers.get("Sec-Fetch-Site")
            if site and site not in ("same-origin", "none"):
                return False
            origin = self.headers.get("Origin")
            if origin:
                host = self.headers.get("Host", "")
                return urlsplit(origin).netloc == host
            return True

        # --- response helpers -----------------------------------------------

        def _reject(self, code: int, text: str) -> None:
            payload = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _serve_bytes(
            self, payload: bytes, content_type: str, write_body: bool
        ) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            if getattr(self, "_set_cookie", False):
                self.send_header(
                    "Set-Cookie",
                    f"{_COOKIE_NAME}={token}; HttpOnly; SameSite=Strict; Path=/",
                )
            self.end_headers()
            if write_body:
                self.wfile.write(payload)

        # --- index ----------------------------------------------------------

        def _serve_index(self, write_body: bool) -> None:
            payload = index_html.encode("utf-8")
            self._serve_bytes(payload, "text/html; charset=utf-8", write_body)

        # --- file -----------------------------------------------------------

        def _serve_file(self, slug: str, write_body: bool) -> None:
            path = files.get(unquote(slug))
            if path is None or not path.is_file():
                self._reject(404, "not found")
                return
            try:
                data = path.read_bytes()
            except OSError:
                self._reject(500, "unreadable")
                return
            self._serve_bytes(data, "text/html; charset=utf-8", write_body)

        # --- prelude (shared by all routes) ---------------------------------

        def _prelude(self) -> bool:
            if not self._authorised():
                self._reject(403, "forbidden")
                return False
            if not self._host_allowed():
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
            target = self.path
            # Strip leading slash, query string, and fragment for routing.
            raw = urlsplit(target).path
            slug = raw[1:] if raw.startswith("/") else raw
            if not slug:
                self._serve_index(write_body=True)
            else:
                self._serve_file(slug, write_body=True)

        def do_HEAD(self) -> None:
            if not self._prelude():
                return
            target = self.path
            raw = urlsplit(target).path
            slug = raw[1:] if raw.startswith("/") else raw
            if not slug:
                self._serve_index(write_body=False)
            else:
                self._serve_file(slug, write_body=False)

        # --- block everything else ------------------------------------------

        def do_POST(self) -> None:
            self._reject(405, "method not allowed")

        do_PUT = do_POST
        do_DELETE = do_POST
        do_PATCH = do_POST
        do_OPTIONS = do_POST

    return Handler


def _cookie_value(header: str) -> str:
    for part in header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == _COOKIE_NAME:
            return value
    return ""


# ── server ──────────────────────────────────────────────────────────────────


class _LibraryServer(ThreadingHTTPServer):
    """``ThreadingHTTPServer`` that actually refuses a port already in use.

    ``http.server`` sets ``allow_reuse_address = 1``, which on POSIX only
    relaxes TIME_WAIT — the reason it is wanted, so a restart is not
    blocked for a minute.  On Windows ``SO_REUSEADDR`` means something else
    entirely: it lets a second process bind a port another process is
    *actively listening on*.  The bind then succeeds, two services claim
    the port, and which one answers is undefined — precisely the "appears
    to start" failure the spec rates as worse than dying.  Off on Windows,
    unchanged everywhere else.
    """

    allow_reuse_address = os.name != "nt"


def serve(
    files: dict[str, pathlib.Path],
    token: str,
    hosts: frozenset[str],
    index_html: str,
    host: str,
    port: int,
) -> None:
    handler = make_handler(files, token, hosts, index_html)
    httpd = _LibraryServer((host, port), handler)
    bound_host, bound_port = httpd.server_address[:2]
    print(f"serve: {len(files)} scripts on http://{bound_host}:{bound_port}")
    httpd.serve_forever()


# ── CLI ─────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="coldread-serve",
        description="Serve a ColdRead read-view library over HTTP.",
    )
    parser.add_argument(
        "--library",
        required=True,
        type=pathlib.Path,
        help="directory whose immediate subdirectories are channels",
    )
    parser.add_argument("--token-file", type=pathlib.Path, help="token file path")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--host-allow",
        action="append",
        default=[],
        dest="host_allow",
        help="extra hostname or IP to allow (repeatable)",
    )
    args = parser.parse_args(argv)

    # --- library directory --------------------------------------------------
    try:
        files, index_html, count = build_library(args.library)
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # --- token --------------------------------------------------------------
    token_path = args.token_file or (
        pathlib.Path.home() / ".config" / "coldread" / "serve.token"
    )
    try:
        token = load_or_create_token(token_path)
    except OSError as exc:
        print(
            f"error: token file {token_path} unusable ({exc})",
            file=sys.stderr,
        )
        return 2

    # --- host allowlist -----------------------------------------------------
    hosts = set(_detect_hosts())
    hosts.add(args.host)
    hosts.update(args.host_allow)

    # --- serve --------------------------------------------------------------
    try:
        serve(files, token, frozenset(hosts), index_html, args.host, args.port)
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
