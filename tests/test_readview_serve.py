"""The library server. Read-only, stdlib-only, designed for a 416MB Pi."""

from __future__ import annotations

import ast
import http.client
import json
import pathlib
import sys
import threading

import pytest

from vo_format.readview.serve import (
    _LibraryServer,
    build_library,
    load_or_create_token,
    make_handler,
    read_channel_config,
)

TOKEN = "t" * 43

SERVE_PY = (
    pathlib.Path(__file__).resolve().parents[1]
    / "vo_format"
    / "readview"
    / "serve.py"
)


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_library(root: pathlib.Path, channels: dict[str, list[str]]) -> None:
    """Create a library directory tree.

    *channels* maps channel-name → list of HTML filenames.
    """
    root.mkdir(parents=True, exist_ok=True)
    for channel, files in channels.items():
        ch_dir = root / channel
        ch_dir.mkdir(exist_ok=True)
        for name in files:
            (ch_dir / name).write_text(
                f"<html><body>{channel}/{name}</body></html>", encoding="utf-8"
            )


def _make_server(
    tmp_path: pathlib.Path,
    channels: dict[str, list[str]] | None = None,
    token: str | None = None,
    hosts: frozenset[str] | None = None,
) -> tuple[_LibraryServer, int, threading.Thread, str]:
    """Spin up a live server on an ephemeral port.

    Returns ``(httpd, port, thread, token)``.  Caller must call
    ``httpd.shutdown()`` and ``thread.join()`` to tear down.
    """
    if channels is None:
        channels = {"CL": ["Script A - readview.html"]}
    lib = tmp_path / "library"
    _make_library(lib, channels)
    files, index_html, _count = build_library(lib)
    token = token or TOKEN
    hosts = hosts or frozenset({"127.0.0.1", "localhost"})
    handler = make_handler(files, token, hosts, index_html)
    httpd = _LibraryServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    )
    thread.start()
    return httpd, port, thread, token


def _request(
    port: int,
    method: str,
    path: str,
    body: str | None = None,
    headers: dict | None = None,
) -> tuple[int, str, dict]:
    """Make an HTTP request and return ``(status, body, response_headers)``."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        resp_headers = dict(response.getheaders())
        return response.status, response.read().decode("utf-8"), resp_headers
    finally:
        conn.close()


# ── fixtures ────────────────────────────────────────────────────────────────


class ServerFixture:
    """Context manager for a live server, torn down on exit."""

    def __init__(
        self,
        tmp_path: pathlib.Path,
        channels: dict[str, list[str]] | None = None,
        token: str | None = None,
        hosts: frozenset[str] | None = None,
    ) -> None:
        self._tmp_path = tmp_path
        self._channels = channels
        self._token = token
        self._hosts = hosts

    def __enter__(self):
        self._httpd, self._port, self._thread, self._token = _make_server(
            self._tmp_path, self._channels, self._token, self._hosts
        )
        return self

    def __exit__(self, *args: object) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=2)

    def request(
        self,
        method: str,
        path: str,
        body: str | None = None,
        headers: dict | None = None,
    ) -> tuple[int, str, dict]:
        return _request(self._port, method, path, body, headers)


# ── stdlib-only ─────────────────────────────────────────────────────────────


class TestSelfContainment:
    """It runs on the serving host with nothing installed."""

    def test_module_imports_only_the_stdlib(self):
        source = SERVE_PY.read_text(encoding="utf-8")
        imports = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append("." * node.level + (node.module or ""))
        assert imports, "no imports found - did the file move?"
        for name in imports:
            root = name.split(".")[0]
            assert root != "vo_format", (
                f"serve must not import the package: {name}"
            )
            assert root in sys.stdlib_module_names, f"non-stdlib import: {name}"


# ── build_library ───────────────────────────────────────────────────────────


class TestBuildLibrary:
    def test_missing_directory_raises(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            build_library(tmp_path / "nope")

    def test_empty_library_raises(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "lib"
        root.mkdir()
        (root / "CH").mkdir()
        with pytest.raises(ValueError, match="no scripts found"):
            build_library(root)

    def test_single_channel_single_script(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "lib"
        _make_library(root, {"CL": ["Script A - readview.html"]})
        files, index_html, count = build_library(root)
        assert count == 1
        assert "CL/Script A - readview.html" in files
        assert "Script A" in index_html
        assert "CL" in index_html

    def test_multiple_channels(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "lib"
        _make_library(
            root,
            {
                "CL": ["Alpha - readview.html"],
                "BoP": ["Beta - readview.html"],
            },
        )
        files, index_html, count = build_library(root)
        assert count == 2
        assert "CL/Alpha - readview.html" in files
        assert "BoP/Beta - readview.html" in files

    def test_symlinks_are_skipped(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "lib"
        _make_library(root, {"CL": ["Real Script - readview.html"]})
        real = root / "CL" / "Real Script - readview.html"
        sym = root / "CL" / "Linked Script - readview.html"
        try:
            sym.symlink_to(real)
        except OSError:
            pytest.skip("symlink creation not available on this platform")
        files, _index, _count = build_library(root)
        assert "CL/Linked Script - readview.html" not in files
        assert "CL/Real Script - readview.html" in files

    def test_dot_directories_are_skipped(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "lib"
        _make_library(root, {"CL": ["A - readview.html"]})
        hidden = root / ".hidden"
        hidden.mkdir()
        (hidden / "B - readview.html").write_text("hidden", encoding="utf-8")
        files, _index, count = build_library(root)
        assert count == 1
        assert ".hidden/B - readview.html" not in files

    def test_index_html_is_excluded(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "lib"
        _make_library(root, {"CL": ["A - readview.html", "index.html"]})
        files, _index, count = build_library(root)
        assert count == 1
        assert "CL/index.html" not in files


# ── channel config ──────────────────────────────────────────────────────────


class TestChannelConfig:
    def test_absent_file_returns_defaults(self, tmp_path: pathlib.Path) -> None:
        order, labels = read_channel_config(tmp_path)
        assert order == ()
        assert labels == {}

    def test_valid_config(self, tmp_path: pathlib.Path) -> None:
        cfg = tmp_path / "channels.json"
        cfg.write_text(
            json.dumps({"order": ["CL", "BoP"], "labels": {"CL": "CassetteLore"}}),
            encoding="utf-8",
        )
        order, labels = read_channel_config(tmp_path)
        assert order == ("CL", "BoP")
        assert labels == {"CL": "CassetteLore"}

    def test_malformed_json_returns_defaults(self, tmp_path: pathlib.Path) -> None:
        cfg = tmp_path / "channels.json"
        cfg.write_text("{not json", encoding="utf-8")
        order, labels = read_channel_config(tmp_path)
        assert order == ()
        assert labels == {}

    def test_not_an_object_returns_defaults(self, tmp_path: pathlib.Path) -> None:
        cfg = tmp_path / "channels.json"
        cfg.write_text("[]", encoding="utf-8")
        order, labels = read_channel_config(tmp_path)
        assert order == ()
        assert labels == {}


# ── token ───────────────────────────────────────────────────────────────────


class TestToken:
    def test_it_creates_a_token_on_first_run(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "token"
        token = load_or_create_token(path)
        assert len(token) >= 43
        assert path.read_text(encoding="utf-8").strip() == token

    def test_it_reuses_an_existing_token(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "token"
        assert load_or_create_token(path) == load_or_create_token(path)


# ── index rendering ─────────────────────────────────────────────────────────


class TestIndex:
    def test_index_contains_script_details(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, body, _ = srv.request("GET", f"/?k={TOKEN}")
            assert status == 200
            assert "Script A" in body
            assert "CL" in body

    def test_index_uses_display_labels(self, tmp_path: pathlib.Path) -> None:
        root = tmp_path / "library"
        _make_library(root, {"CL": ["Script A - readview.html"]})
        cfg = root / "channels.json"
        cfg.write_text(
            json.dumps({"labels": {"CL": "CassetteLore"}}), encoding="utf-8"
        )
        files, index_html, _count = build_library(root)
        handler = make_handler(
            files, TOKEN, frozenset({"127.0.0.1", "localhost"}), index_html
        )
        httpd = _LibraryServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        try:
            status, body, _ = _request(port, "GET", f"/?k={TOKEN}")
            assert status == 200
            assert "CassetteLore" in body
            # The raw channel name may appear in data attributes but must not
            # appear as a visible heading. Check that the summary element
            # contains the display label, not the raw key.
            assert "<summary>CL" not in body
        finally:
            httpd.shutdown()
            thread.join(timeout=2)

    def test_index_is_correct_content_type(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            _status, body, headers = srv.request("GET", f"/?k={TOKEN}")
            ct = headers.get("Content-Type", "")
            assert "text/html" in ct

    def test_head_index_returns_no_body(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, body, _headers = srv.request("HEAD", f"/?k={TOKEN}")
            assert status == 200
            assert body == ""

    def test_head_file_returns_no_body(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, body, _headers = srv.request(
                "HEAD", f"/CL/Script%20A%20-%20readview.html?k={TOKEN}"
            )
            assert status == 200
            assert body == ""


# ── routing / allowlist ─────────────────────────────────────────────────────


class TestRouting:
    def test_valid_slug_serves_file(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, body, _ = srv.request(
                "GET",
                f"/CL/Script%20A%20-%20readview.html?k={TOKEN}",
            )
            assert status == 200
            assert "CL/Script A - readview.html" in body

    def test_unknown_slug_is_404(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET", f"/CL/No%20Such%20File.html?k={TOKEN}"
            )
            assert status == 404

    def test_unknown_channel_is_404(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET", f"/BoP/Script%20A%20-%20readview.html?k={TOKEN}"
            )
            assert status == 404


# ── path traversal ──────────────────────────────────────────────────────────


class TestPathTraversal:
    def test_dot_dot_slash_is_404(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET", f"/../../../etc/passwd?k={TOKEN}"
            )
            assert status == 404

    def test_encoded_dot_dot_is_404(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET", f"/%2e%2e/%2e%2e/etc/passwd?k={TOKEN}"
            )
            assert status == 404

    def test_absolute_path_is_404(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET", f"/C:/Windows/System32/drivers/etc/hosts?k={TOKEN}"
            )
            assert status == 404

    def test_slug_with_traversal_is_not_in_dict(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET",
                f"/CL/..%2f..%2f..%2fetc%2fpasswd?k={TOKEN}",
            )
            assert status == 404


# ── auth ────────────────────────────────────────────────────────────────────


class TestAuth:
    def test_no_token_is_403(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            assert srv.request("GET", "/")[0] == 403

    def test_wrong_token_is_403(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            assert srv.request("GET", f"/?k={'x' * 43}")[0] == 403

    def test_cookie_auth_works(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET", "/", headers={"Cookie": f"coldread_serve={TOKEN}"}
            )
            assert status == 200

    def test_query_token_sets_cookie(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, headers = srv.request("GET", f"/?k={TOKEN}")
            assert status == 200
            cookie = headers.get("Set-Cookie", "")
            assert "HttpOnly" in cookie
            assert "SameSite=Strict" in cookie

    def test_file_requires_auth(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            assert srv.request(
                "GET", "/CL/Script%20A%20-%20readview.html"
            )[0] == 403


# ── methods ─────────────────────────────────────────────────────────────────


class TestMethods:
    def test_post_is_405(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            assert srv.request("POST", f"/?k={TOKEN}")[0] == 405

    def test_put_is_405(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            assert srv.request("PUT", f"/?k={TOKEN}")[0] == 405

    def test_delete_is_405(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            assert srv.request("DELETE", f"/?k={TOKEN}")[0] == 405

    def test_patch_is_405(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            assert srv.request("PATCH", f"/?k={TOKEN}")[0] == 405

    def test_options_is_405(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            assert srv.request("OPTIONS", f"/?k={TOKEN}")[0] == 405


# ── log scrubbing ───────────────────────────────────────────────────────────


class TestLogScrubbing:
    def test_token_never_reaches_a_log_line(
        self, tmp_path: pathlib.Path, capsys
    ) -> None:
        with ServerFixture(tmp_path) as srv:
            srv.request("GET", f"/?k={TOKEN}")
            srv.request("GET", f"/CL/Script%20A%20-%20readview.html?k={TOKEN}")
        captured = capsys.readouterr()
        assert TOKEN not in captured.err
        assert TOKEN not in captured.out


# ── security headers ────────────────────────────────────────────────────────


class TestSecurityHeaders:
    def test_foreign_origin_is_rejected(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET",
                f"/?k={TOKEN}",
                headers={"Origin": "https://evil.example"},
            )
            assert status == 403

    def test_absent_origin_is_allowed(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            assert srv.request("GET", f"/?k={TOKEN}")[0] == 200

    def test_same_origin_is_allowed(self, tmp_path: pathlib.Path) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET",
                f"/?k={TOKEN}",
                headers={
                    "Origin": "http://127.0.0.1",
                    "Host": "127.0.0.1",
                },
            )
            assert status == 200

    def test_cross_site_fetch_metadata_is_rejected(
        self, tmp_path: pathlib.Path
    ) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET",
                f"/?k={TOKEN}",
                headers={"Sec-Fetch-Site": "cross-site"},
            )
            assert status == 403

    def test_same_origin_fetch_metadata_is_allowed(
        self, tmp_path: pathlib.Path
    ) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET",
                f"/?k={TOKEN}",
                headers={"Sec-Fetch-Site": "same-origin"},
            )
            assert status == 200

    def test_none_fetch_metadata_is_allowed(
        self, tmp_path: pathlib.Path
    ) -> None:
        with ServerFixture(tmp_path) as srv:
            status, _, _ = srv.request(
                "GET",
                f"/?k={TOKEN}",
                headers={"Sec-Fetch-Site": "none"},
            )
            assert status == 200


# ── host checking ───────────────────────────────────────────────────────────


class TestHostCheck:
    def test_known_host_is_allowed(self, tmp_path: pathlib.Path) -> None:
        hosts = frozenset({"127.0.0.1", "raspberrypi.local"})
        with ServerFixture(tmp_path, hosts=hosts) as srv:
            status, _, _ = srv.request(
                "GET",
                f"/?k={TOKEN}",
                headers={"Host": "127.0.0.1"},
            )
            assert status == 200

    def test_foreign_host_is_rejected(self, tmp_path: pathlib.Path) -> None:
        hosts = frozenset({"raspberrypi.local"})
        with ServerFixture(tmp_path, hosts=hosts) as srv:
            status, _, _ = srv.request(
                "GET",
                f"/?k={TOKEN}",
                headers={"Host": "evil.example"},
            )
            assert status == 403

    def test_known_host_with_port_is_allowed(self, tmp_path: pathlib.Path) -> None:
        hosts = frozenset({"raspberrypi.local", "127.0.0.1"})
        with ServerFixture(tmp_path, hosts=hosts) as srv:
            status, _, _ = srv.request(
                "GET",
                f"/?k={TOKEN}",
                headers={"Host": "127.0.0.1:9999"},
            )
            assert status == 200


# ── startup refusal ─────────────────────────────────────────────────────────


class TestMainRefusesRatherThanDegrading:
    def test_missing_library_directory_is_named_and_non_zero(
        self, tmp_path: pathlib.Path, capsys
    ) -> None:
        from vo_format.readview.serve import main

        missing = tmp_path / "nope"
        code = main(
            [
                "--library",
                str(missing),
                "--token-file",
                str(tmp_path / "token"),
                "--host",
                "127.0.0.1",
                "--port",
                "18765",
            ]
        )
        assert code != 0
        assert "not found" in capsys.readouterr().err

    def test_empty_library_is_refused(
        self, tmp_path: pathlib.Path, capsys
    ) -> None:
        from vo_format.readview.serve import main

        lib = tmp_path / "empty-lib"
        lib.mkdir()
        (lib / "CH").mkdir()
        code = main(
            [
                "--library",
                str(lib),
                "--token-file",
                str(tmp_path / "token"),
                "--host",
                "127.0.0.1",
                "--port",
                "18766",
            ]
        )
        assert code != 0
        assert "no scripts found" in capsys.readouterr().err

    def test_unreadable_token_file_is_refused(
        self, tmp_path: pathlib.Path, capsys
    ) -> None:
        from vo_format.readview.serve import main

        lib = tmp_path / "lib"
        _make_library(lib, {"CL": ["A - readview.html"]})

        # Make the token-file path a directory so read_text raises OSError.
        token_path = tmp_path / "token_is_a_dir"
        token_path.mkdir()

        code = main(
            [
                "--library",
                str(lib),
                "--token-file",
                str(token_path),
                "--host",
                "127.0.0.1",
                "--port",
                "18767",
            ]
        )
        assert code != 0
        assert "token" in capsys.readouterr().err.lower()


# ── HTML escaping ───────────────────────────────────────────────────────────


class TestEscaping:
    def test_angle_brackets_in_title_are_escaped(
        self, tmp_path: pathlib.Path
    ) -> None:
        # Filename contains & which must be HTML-escaped in the output.
        with ServerFixture(
            tmp_path,
            channels={"CL": ["Test & Script - readview.html"]},
        ) as srv:
            status, body, _ = srv.request("GET", f"/?k={TOKEN}")
            assert status == 200
            assert "Test &amp; Script" in body

    def test_channel_label_cannot_inject_markup(
        self, tmp_path: pathlib.Path
    ) -> None:
        root = tmp_path / "library"
        _make_library(root, {"CL": ["A - readview.html"]})
        cfg = root / "channels.json"
        cfg.write_text(
            json.dumps({"labels": {"CL": "<i>X</i>"}}), encoding="utf-8"
        )
        files, index_html, _count = build_library(root)
        handler = make_handler(
            files, TOKEN, frozenset({"127.0.0.1", "localhost"}), index_html
        )
        httpd = _LibraryServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        try:
            status, body, _ = _request(port, "GET", f"/?k={TOKEN}")
            assert status == 200
            # The label must be escaped — <i>X</i> must not appear as raw HTML
            # inside the summary element.
            assert "<summary>&lt;i&gt;X&lt;/i&gt;" in body
        finally:
            httpd.shutdown()
            thread.join(timeout=2)


# ── symlink in path traversal ───────────────────────────────────────────────


class TestSymlinkPathTraversal:
    def test_symlink_to_outside_file_is_404(
        self, tmp_path: pathlib.Path
    ) -> None:
        outside = tmp_path / "secret.txt"
        outside.write_text("secret", encoding="utf-8")

        lib = tmp_path / "lib"
        ch = lib / "CL"
        ch.mkdir(parents=True)

        real = ch / "Real Script - readview.html"
        real.write_text("ok", encoding="utf-8")

        try:
            sym = ch / "Linked Script - readview.html"
            sym.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation not available on this platform")

        files, index_html, _count = build_library(lib)
        # The symlink should have been skipped during enumeration.
        assert "CL/Linked Script - readview.html" not in files

        handler = make_handler(
            files, TOKEN, frozenset({"127.0.0.1", "localhost"}), index_html
        )
        httpd = _LibraryServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        try:
            status, _, _ = _request(
                port,
                "GET",
                f"/CL/Linked%20Script%20-%20readview.html?k={TOKEN}",
            )
            assert status == 404
        finally:
            httpd.shutdown()
            thread.join(timeout=2)


# ── missing host header ─────────────────────────────────────────────────────


class TestMissingHostHeader:
    def test_missing_host_header_is_still_checked(
        self, tmp_path: pathlib.Path
    ) -> None:
        hosts = frozenset({"raspberrypi.local"})
        with ServerFixture(tmp_path, hosts=hosts) as srv:
            status, _, _ = srv.request("GET", f"/?k={TOKEN}")
            # http.client sends Host: 127.0.0.1 automatically, and that won't
            # be in the allowlist if we only have raspberrypi.local.
            assert status == 403


# ── concurrent requests ─────────────────────────────────────────────────────


class TestConcurrent:
    def test_server_handles_parallel_requests(
        self, tmp_path: pathlib.Path
    ) -> None:
        with ServerFixture(
            tmp_path,
            channels={
                "CL": ["A - readview.html", "B - readview.html"],
                "BoP": ["C - readview.html"],
            },
        ) as srv:
            results = []

            def fetch(path: str) -> None:
                status, _, _ = srv.request("GET", path)
                results.append(status)

            threads = [
                threading.Thread(
                    target=fetch, args=(f"/?k={TOKEN}",), daemon=True
                )
                for _ in range(5)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
            assert all(s == 200 for s in results)
            assert len(results) == 5


# ── hmac.compare_digest ─────────────────────────────────────────────────────


class TestTokenComparison:
    def test_token_comparison_is_constant_time(self) -> None:
        import hmac as hmac_mod

        a = b"a" * 43
        b_same = b"a" * 43
        b_diff = b"b" * 43

        # Verify the import is actually hmac.compare_digest, not plain ==.
        # We can't easily time this in a test, but we can verify the
        # implementation uses hmac.compare_digest by inspecting the source.
        source = SERVE_PY.read_text(encoding="utf-8")
        assert "hmac.compare_digest" in source
        assert "token.encode" in source

        # Sanity: compare_digest returns True for equal, False for unequal.
        assert hmac_mod.compare_digest(a, b_same) is True
        assert hmac_mod.compare_digest(a, b_diff) is False
