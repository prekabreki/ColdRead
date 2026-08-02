# Shared Read State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the read-view and library pages one shared state on the serving host, so read marks, completed scripts and preferences follow the reader between phone and desktop — without the read-view losing the ability to work with the network off.

**Architecture:** A stdlib-only HTTP service (`state.py`) owns a single JSON file: `GET /state` returns it, `POST /state` merges a patch, everything else is 405. Merge is per-field last-write-wins on timestamps the *server* assigns, back-dated by an age the client supplies, so a late flush from an offline session cannot overwrite a newer edit. Clients (`sync.js`) treat `localStorage` as an offline-first cache behind a persisted write queue. The seam is the identical `store` object already present in both `reader.js` and `library.py`, so no existing call site changes.

**Tech Stack:** Python 3.10+ stdlib only in `state.py` (`http.server`, `json`, `hmac`, `secrets`, `threading`, `os.replace`) — no PyPI, no `vo_format` imports. Vanilla ES5-style JS. pytest.

**Spec:** `docs/superpowers/specs/2026-08-02-shared-read-state-design.md`

**Sequencing:** This plan must land AFTER `2026-08-02-hud-speed-and-countdown.md`. Both modify `render.py` and `reader.js`; running them concurrently will conflict.

## Global Constraints

- `state.py` imports **only** the standard library — nothing from PyPI, nothing from `vo_format`. It runs on a host with nothing installed. Enforced by an AST test (Task 7).
- `library.py` keeps the same constraint it has today and for the same reason: it is executed as `python3 - <dir>` with its source on stdin, so it has **no `__file__`** and cannot read a sibling file. Every asset it needs is an inline literal.
- `MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000`. `PORT` default `8766`. Bind default `127.0.0.1`.
- No device's wall clock may enter the merge. Clients send **durations**, the server assigns **timestamps**.
- Sync is opt-in via `--sync HREF` and inert unless `location.protocol` starts with `http`. A read-view opened as a file keeps working, silently local.
- The rendered page stays self-contained: no `<script src>`, no `<link>`, no remote URL. Existing tests in `tests/test_readview_render.py::TestSelfContainment` enforce it.
- `ruff` line length 88. New tests must be Windows-clean: never `subprocess.run(..., text=True)` without `encoding="utf-8"`.
- Run Python as `./.venv/bin/python` (`.\.venv\Scripts\python.exe` on Windows).

---

### Task 1: The merge core

Pure functions, no I/O, no HTTP. This is where every subtle rule lives, so it is tested exhaustively before anything can call it.

**Files:**
- Create: `vo_format/readview/state.py`
- Test: `tests/test_readview_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces, and later tasks depend on these exact names and signatures:
  - `MAX_AGE_MS: int`
  - `clamp_age(age: object) -> int` — any junk becomes a sane non-negative ms value
  - `Clock` with `.now() -> int` — monotonic-by-construction millisecond source
  - `merge_field(current: dict | None, value: object, t: int) -> dict | None` — returns the new `{"v":…, "t":…}`, or `None` meaning "keep what you have"
  - `apply_patch(fields: dict, patch: dict, clock: Clock) -> dict` — mutates and returns `fields`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_readview_state.py`:

```python
"""The merge rules. Every one of these encodes a way state can silently vanish."""

from __future__ import annotations

from vo_format.readview.state import (
    MAX_AGE_MS,
    Clock,
    apply_patch,
    clamp_age,
    merge_field,
)


class TestClampAge:
    def test_a_plain_age_survives(self) -> None:
        assert clamp_age(1500) == 1500

    def test_a_missing_age_means_now(self) -> None:
        assert clamp_age(None) == 0

    def test_junk_means_now_rather_than_raising(self) -> None:
        # A malformed field must not take the whole request down with it.
        assert clamp_age("soon") == 0
        assert clamp_age({}) == 0

    def test_a_negative_age_means_now(self) -> None:
        # The device's clock moved backwards between enqueue and flush.
        assert clamp_age(-9999) == 0

    def test_an_absurd_age_is_capped(self) -> None:
        assert clamp_age(MAX_AGE_MS * 100) == MAX_AGE_MS

    def test_a_float_age_is_accepted(self) -> None:
        assert clamp_age(1500.7) == 1500


class TestClock:
    def test_it_never_goes_backwards(self) -> None:
        # The guard is on the BASE reading, not on the finished stamp: stamps are
        # deliberately back-dated by age, so guarding the stamp would forbid the
        # back-dating that makes late flushes correct.
        clock = Clock(source=iter([1000, 900, 900, 1005]).__next__)
        assert [clock.now() for _ in range(4)] == [1000, 1001, 1002, 1003]

    def test_it_follows_a_forward_clock(self) -> None:
        clock = Clock(source=iter([1000, 2000]).__next__)
        assert [clock.now() for _ in range(2)] == [1000, 2000]


class TestMergeField:
    def test_a_new_field_is_written(self) -> None:
        assert merge_field(None, True, 500) == {"v": True, "t": 500}

    def test_a_newer_write_wins(self) -> None:
        assert merge_field({"v": False, "t": 400}, True, 500) == {"v": True, "t": 500}

    def test_an_older_write_loses(self) -> None:
        # The offline-flush case: arrived late, but happened earlier.
        assert merge_field({"v": True, "t": 600}, False, 500) is None

    def test_an_equal_timestamp_loses_so_a_replay_is_idempotent(self) -> None:
        assert merge_field({"v": True, "t": 500}, False, 500) is None

    def test_false_is_a_value_not_an_absence(self) -> None:
        # Un-marking is a tombstone. A deletion carries no timestamp and so
        # could never outrank a stale True; this is why we write False.
        assert merge_field({"v": True, "t": 400}, False, 500) == {"v": False, "t": 500}

    def test_a_structured_value_survives_intact(self) -> None:
        mark = {"line": 42, "start": 3, "end": 10, "text": "whisper"}
        assert merge_field(None, mark, 500) == {"v": mark, "t": 500}


class TestApplyPatch:
    def _clock(self, *values: int) -> Clock:
        return Clock(source=iter(values).__next__)

    def test_it_stamps_with_the_servers_clock(self) -> None:
        fields: dict = {}
        apply_patch(fields, {"read": {"a.html": {"v": True}}}, self._clock(1000))
        assert fields == {"read": {"a.html": {"v": True, "t": 1000}}}

    def test_an_age_back_dates_the_stamp(self) -> None:
        fields: dict = {}
        apply_patch(
            fields, {"read": {"a.html": {"v": True, "age_ms": 300}}}, self._clock(1000)
        )
        assert fields["read"]["a.html"]["t"] == 700

    def test_a_late_flush_does_not_clobber_a_newer_edit(self) -> None:
        # THE case this whole mechanism exists for. The booth device reconnects
        # last but acted first, so it must lose.
        fields = {"read": {"a.html": {"v": False, "t": 900}}}
        apply_patch(
            fields,
            {"read": {"a.html": {"v": True, "age_ms": 500}}},   # happened at 500
            self._clock(1000),
        )
        assert fields["read"]["a.html"] == {"v": False, "t": 900}

    def test_two_devices_marking_different_scripts_both_survive(self) -> None:
        # Why timestamps live at field level and not namespace level.
        fields = {"read": {"a.html": {"v": True, "t": 900}}}
        apply_patch(fields, {"read": {"b.html": {"v": True}}}, self._clock(1000))
        assert set(fields["read"]) == {"a.html", "b.html"}

    def test_a_new_namespace_is_created(self) -> None:
        fields: dict = {}
        apply_patch(fields, {"script:A": {"wpm": {"v": 165}}}, self._clock(1000))
        assert fields["script:A"]["wpm"]["v"] == 165

    def test_a_malformed_namespace_is_skipped_not_fatal(self) -> None:
        fields: dict = {}
        apply_patch(fields, {"read": "not a dict"}, self._clock(1000))
        assert fields == {}

    def test_a_field_without_a_v_key_is_skipped(self) -> None:
        fields: dict = {}
        apply_patch(fields, {"read": {"a.html": {"age_ms": 5}}}, self._clock(1000))
        assert fields == {}

    def test_it_returns_the_same_object_it_mutated(self) -> None:
        fields: dict = {}
        assert apply_patch(fields, {}, self._clock(1000)) is fields
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'vo_format.readview.state'`.

- [ ] **Step 3: Write the merge core**

Create `vo_format/readview/state.py`:

```python
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

import time
from typing import Callable

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
    """
    if not isinstance(patch, dict):
        return fields
    for namespace, incoming in patch.items():
        if not isinstance(incoming, dict):
            continue
        target = fields.setdefault(namespace, {})
        if not isinstance(target, dict):
            continue
        for name, entry in incoming.items():
            if not isinstance(entry, dict) or "v" not in entry:
                continue
            t = clock.now() - clamp_age(entry.get("age_ms"))
            merged = merge_field(target.get(name), entry["v"], t)
            if merged is not None:
                target[name] = merged
    return fields
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py -v`
Expected: PASS, all of them.

Run: `./.venv/bin/ruff check vo_format/readview/state.py tests/test_readview_state.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add vo_format/readview/state.py tests/test_readview_state.py
git commit -m "Add the shared-state merge core: per-field LWW with server stamps"
```

---

### Task 2: The store — load, snapshot, atomic save

**Files:**
- Modify: `vo_format/readview/state.py`
- Test: `tests/test_readview_state.py`

**Interfaces:**
- Consumes: `Clock`, `apply_patch` from Task 1.
- Produces:
  - `Store(path: pathlib.Path, clock: Clock | None = None)`
  - `.fields: dict`
  - `.load() -> None` — tolerant; a corrupt or absent file yields `{}` and a stderr line
  - `.snapshot() -> dict` — `{"now": int, "fields": dict}`
  - `.apply(patch: dict) -> dict` — merges, saves, returns `snapshot()`
  - `.save() -> None` — atomic, with one rotating `.bak`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_readview_state.py`:

```python
import json
import pathlib

import pytest

from vo_format.readview.state import Store


class TestStore:
    def test_an_absent_file_loads_as_empty(self, tmp_path: pathlib.Path) -> None:
        store = Store(tmp_path / "state.json")
        store.load()
        assert store.fields == {}

    def test_a_corrupt_file_loads_as_empty_and_says_so(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # It must NOT raise: refusing to start would take the library down over
        # a preferences file. But it must not be silent either — a page that
        # believes it is synced against nothing is the worst outcome here.
        path = tmp_path / "state.json"
        path.write_text("{not json", encoding="utf-8")
        store = Store(path)
        store.load()
        assert store.fields == {}
        assert "state.json" in capsys.readouterr().err

    def test_a_snapshot_carries_now_and_fields(self, tmp_path: pathlib.Path) -> None:
        store = Store(tmp_path / "state.json")
        store.load()
        snap = store.snapshot()
        assert set(snap) == {"now", "fields"}
        assert isinstance(snap["now"], int)

    def test_apply_persists_to_disk(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "state.json"
        store = Store(path)
        store.load()
        store.apply({"read": {"a.html": {"v": True}}})
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["read"]["a.html"]["v"] is True

    def test_a_round_trip_survives_a_reload(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "state.json"
        first = Store(path)
        first.load()
        first.apply({"read": {"a.html": {"v": True}}})
        second = Store(path)
        second.load()
        assert second.fields["read"]["a.html"]["v"] is True

    def test_saving_leaves_no_temp_file_behind(self, tmp_path: pathlib.Path) -> None:
        path = tmp_path / "state.json"
        store = Store(path)
        store.load()
        store.apply({"read": {"a.html": {"v": True}}})
        assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]

    def test_the_second_save_keeps_a_backup(self, tmp_path: pathlib.Path) -> None:
        # The state file is the only thing on the serving host that is not
        # reconstructible from a repo.
        path = tmp_path / "state.json"
        store = Store(path)
        store.load()
        store.apply({"read": {"a.html": {"v": True}}})
        store.apply({"read": {"b.html": {"v": True}}})
        assert (tmp_path / "state.json.bak").is_file()

    def test_a_reload_after_a_backup_still_reads_the_live_file(
        self, tmp_path: pathlib.Path
    ) -> None:
        path = tmp_path / "state.json"
        store = Store(path)
        store.load()
        store.apply({"read": {"a.html": {"v": True}}})
        store.apply({"read": {"b.html": {"v": True}}})
        fresh = Store(path)
        fresh.load()
        assert set(fresh.fields["read"]) == {"a.html", "b.html"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py::TestStore -v`
Expected: FAIL — `ImportError: cannot import name 'Store'`.

- [ ] **Step 3: Implement the store**

Add to `vo_format/readview/state.py`. Extend the import block at the top with
`import json`, `import os`, `import pathlib` and `import sys`:

```python
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
            print(f"state: {self.path} unreadable ({exc}); starting empty",
                  file=sys.stderr)
            self.fields = {}
            return
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            print(f"state: {self.path} is not valid JSON ({exc}); starting empty",
                  file=sys.stderr)
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add vo_format/readview/state.py tests/test_readview_state.py
git commit -m "Add the state store with atomic saves and a rotating backup"
```

---

### Task 3: The HTTP surface

**Files:**
- Modify: `vo_format/readview/state.py`
- Test: `tests/test_readview_state.py`

**Interfaces:**
- Consumes: `Store` from Task 2.
- Produces:
  - `make_handler(store: Store, token: str, lock: threading.Lock) -> type` — returns a `BaseHTTPRequestHandler` subclass
  - `serve(store, token, host: str, port: int) -> None`
  - Route contract: `GET /state` → 200 snapshot JSON; `POST /state` → 200 merged snapshot; anything else → 404 for unknown paths, 405 for unknown methods, 403 for auth failures.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_readview_state.py`. Drive the handler over a real socket —
the auth, method and header rules only exist at the HTTP layer, and asserting
them against the class rather than a request proves nothing:

```python
import http.client
import threading
from http.server import ThreadingHTTPServer

from vo_format.readview.state import make_handler

TOKEN = "t" * 43


class _Server:
    """A live server on an ephemeral port, for one test."""

    def __init__(self, tmp_path: pathlib.Path) -> None:
        store = Store(tmp_path / "state.json")
        store.load()
        handler = make_handler(store, TOKEN, threading.Lock())
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, body: str | None = None,
                headers: dict | None = None) -> tuple[int, str]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            conn.close()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server(tmp_path: pathlib.Path):
    live = _Server(tmp_path)
    yield live
    live.close()


class TestHttp:
    def test_get_with_the_token_returns_a_snapshot(self, server) -> None:
        status, body = server.request("GET", f"/state?k={TOKEN}")
        assert status == 200
        assert set(json.loads(body)) == {"now", "fields"}

    def test_get_without_a_token_is_forbidden(self, server) -> None:
        assert server.request("GET", "/state")[0] == 403

    def test_a_wrong_token_is_forbidden(self, server) -> None:
        assert server.request("GET", f"/state?k={'x' * 43}")[0] == 403

    def test_the_token_can_arrive_as_a_cookie(self, server) -> None:
        status, _ = server.request(
            "GET", "/state", headers={"Cookie": f"coldread_state={TOKEN}"}
        )
        assert status == 200

    def test_a_query_token_sets_the_cookie(self, server) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        conn.request("GET", f"/state?k={TOKEN}")
        response = conn.getresponse()
        response.read()
        cookie = response.getheader("Set-Cookie") or ""
        conn.close()
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie

    def test_post_merges_and_returns_the_new_state(self, server) -> None:
        body = json.dumps({"fields": {"read": {"a.html": {"v": True}}}})
        status, out = server.request(
            "POST", f"/state?k={TOKEN}", body=body,
            headers={"Content-Type": "application/json"},
        )
        assert status == 200
        assert json.loads(out)["fields"]["read"]["a.html"]["v"] is True

    def test_a_foreign_origin_is_rejected(self, server) -> None:
        status, _ = server.request(
            "POST", f"/state?k={TOKEN}", body="{}",
            headers={"Origin": "https://evil.example"},
        )
        assert status == 403

    def test_an_absent_origin_is_allowed(self, server) -> None:
        # A bookmark tap sends no Origin. Rejecting absence breaks the only way
        # this page is ever opened.
        assert server.request("POST", f"/state?k={TOKEN}", body="{}")[0] == 200

    def test_a_cross_site_fetch_metadata_header_is_rejected(self, server) -> None:
        status, _ = server.request(
            "POST", f"/state?k={TOKEN}", body="{}",
            headers={"Sec-Fetch-Site": "cross-site"},
        )
        assert status == 403

    def test_other_methods_are_405(self, server) -> None:
        for method in ("PUT", "DELETE", "PATCH"):
            assert server.request(method, f"/state?k={TOKEN}")[0] == 405

    def test_an_unknown_path_is_404(self, server) -> None:
        assert server.request("GET", f"/nope?k={TOKEN}")[0] == 404

    def test_malformed_json_is_400_not_a_traceback(self, server) -> None:
        status, _ = server.request("POST", f"/state?k={TOKEN}", body="{not json")
        assert status == 400

    def test_the_token_never_reaches_a_log_line(self, server, capsys) -> None:
        server.request("GET", f"/state?k={TOKEN}")
        captured = capsys.readouterr()
        assert TOKEN not in captured.err
        assert TOKEN not in captured.out
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py::TestHttp -v`
Expected: FAIL — `cannot import name 'make_handler'`.

- [ ] **Step 3: Implement the handler**

Add to `state.py`. Extend the imports with `import hmac`, `import threading`,
`from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer`, and
`from urllib.parse import parse_qs, urlsplit`:

```python
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
            if not hmac.compare_digest(supplied, token):
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


def serve(store: Store, token: str, host: str, port: int) -> None:
    handler = make_handler(store, token, threading.Lock())
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"state: serving {store.path} on http://{host}:{port}/state")
    httpd.serve_forever()
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py -v`
Expected: PASS.

Note on `do_HEAD` returning 405: the spec's method surface is GET and POST only.
`http.server` would otherwise answer HEAD by way of `do_GET`, which would leak a
200 to an unauthenticated probe shape nobody has reasoned about.

- [ ] **Step 5: Commit**

```bash
git add vo_format/readview/state.py tests/test_readview_state.py
git commit -m "Add the state service HTTP surface: token auth, origin checks, 405s"
```

---

### Task 4: The CLI, and refusing to start rather than lying

**Files:**
- Modify: `vo_format/readview/state.py`, `pyproject.toml`
- Test: `tests/test_readview_state.py`

**Interfaces:**
- Consumes: `Store`, `serve`.
- Produces: `load_or_create_token(path: pathlib.Path) -> str`, `main(argv: list[str] | None = None) -> int`, console script `coldread-state`.

- [ ] **Step 1: Write the failing tests**

```python
class TestToken:
    def test_it_creates_a_token_on_first_run(self, tmp_path: pathlib.Path) -> None:
        from vo_format.readview.state import load_or_create_token

        path = tmp_path / "token"
        token = load_or_create_token(path)
        assert len(token) >= 43            # 256 bits, urlsafe-base64
        assert path.read_text(encoding="utf-8").strip() == token

    def test_it_reuses_an_existing_token(self, tmp_path: pathlib.Path) -> None:
        from vo_format.readview.state import load_or_create_token

        path = tmp_path / "token"
        assert load_or_create_token(path) == load_or_create_token(path)


class TestMainRefusesRatherThanDegrading:
    def test_an_unwritable_state_directory_is_named_and_non_zero(
        self, tmp_path: pathlib.Path, capsys
    ) -> None:
        from vo_format.readview.state import main

        missing = tmp_path / "nope" / "deeper" / "state.json"
        code = main(["--state-file", str(missing), "--token-file",
                     str(tmp_path / "token"), "--check"])
        assert code != 0
        assert "nope" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py -k "Token or Refuses" -v`
Expected: FAIL on the missing names.

- [ ] **Step 3: Implement**

Add `import argparse` and `import secrets` to the imports, then:

```python
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
        path.chmod(0o600)          # best effort; a no-op on some filesystems
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
    parent = args.state_file.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".coldread-state-write-probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        print(f"error: cannot write beside {args.state_file} ({exc})",
              file=sys.stderr)
        return 2

    try:
        token = load_or_create_token(args.token_file)
    except OSError as exc:
        print(f"error: token file {args.token_file} unusable ({exc})",
              file=sys.stderr)
        return 2

    store = Store(args.state_file)
    store.load()
    if args.check:
        print(f"ok    {args.state_file} writable, token loaded, would bind "
              f"{args.host}:{args.port}")
        return 0
    try:
        serve(store, token, args.host, args.port)
    except OSError as exc:
        print(f"error: cannot bind {args.host}:{args.port} ({exc})",
              file=sys.stderr)
        return 2
    return 0


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
```

In `pyproject.toml`, under `[project.scripts]`, add:

```toml
coldread-state = "vo_format.readview.state:_entry"
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py -v`
Expected: PASS.

- [ ] **Step 5: Prove the bind failure is loud, not silent**

A busy port is the failure most likely to be met in practice, and a service that
appears to start while a *different* process owns the port is worse than one that
dies. Check it by hand:

```bash
./.venv/bin/python -m vo_format.readview.state --state-file /tmp/s.json \
  --token-file /tmp/s.token --port 8766 &
./.venv/bin/python -m vo_format.readview.state --state-file /tmp/s2.json \
  --token-file /tmp/s2.token --port 8766 ; echo "exit=$?"
```

Expected: the second prints a named `error: cannot bind …` and `exit=2`. Kill the
first afterwards. Record the output in the PR.

- [ ] **Step 6: Commit**

```bash
git add vo_format/readview/state.py tests/test_readview_state.py pyproject.toml
git commit -m "Add the coldread-state CLI, refusing to start rather than degrading"
```

---

### Task 5: The sync client

**Files:**
- Create: `vo_format/readview/sync.js`
- Test: `tests/test_readview_state.py`

**Interfaces:**
- Consumes: nothing Python-side.
- Produces: a global factory `coldreadSync(prefix, href)` returning `{get, set, pending, onchange}` — a drop-in replacement for the `store` object in both pages, plus a pending count for the indicator.

- [ ] **Step 1: Write the failing test**

```python
class TestSyncAsset:
    def test_the_asset_exists_and_is_the_expected_shape(self) -> None:
        from importlib.resources import files

        source = (files("vo_format.readview") / "sync.js").read_text(
            encoding="utf-8"
        )
        assert "function coldreadSync(" in source
        # Durations, never wall-clock times: the merge must not depend on any
        # device's idea of what time it is.
        assert "age_ms" in source
        # The queue outlives the page, or a booth session's marks die with it.
        assert "pending" in source
        # keepalive, or the last flush of a session is cancelled by navigation.
        assert "keepalive" in source
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py::TestSyncAsset -v`
Expected: FAIL — the file does not exist.

- [ ] **Step 3: Write `vo_format/readview/sync.js`**

```js
"use strict";
// Shared read state, offline first. localStorage is the truth the page renders
// from; the server is where devices meet. Losing state is acceptable, refusing
// to render is not — the same contract reader.js and library.py already keep.
//
// This file has an identical twin: an inline copy inside library.py, which is
// piped to a remote interpreter and therefore cannot read a sibling asset. A
// test asserts the two are byte-identical. Edit both or neither.
function coldreadSync(prefix, href) {
  var FLUSH_MS = 1000, RETRY_MS = 15000;
  var queueKey = prefix + ":pending";
  var listeners = [];
  var timer = null, retry = null, inflight = false;

  function raw(key, fallback) {
    try {
      var value = localStorage.getItem(key);
      return value === null ? fallback : JSON.parse(value);
    } catch (e) { return fallback; }
  }

  function put(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch (e) { /* in-memory only */ }
  }

  // Sync is inert off http(s): a lone read-view opened as a file keeps working,
  // silently local, and never issues a request its deployment did not ask for.
  var live = !!href && /^https?:/.test(location.protocol);

  function queue() { return raw(queueKey, {}) || {}; }

  function enqueue(namespace, field, value) {
    var q = queue();
    if (!q[namespace]) { q[namespace] = {}; }
    // Date.now() and not performance.now(): the queue outlives the page, and a
    // monotonic counter resets on reload. Only ever used as a DIFFERENCE
    // against this same device, never compared with another device's clock.
    q[namespace][field] = { v: value, at: Date.now() };
    put(queueKey, q);
    announce();
  }

  function pendingCount() {
    var q = queue(), n = 0;
    for (var namespace in q) {
      for (var field in q[namespace]) { n += 1; }
    }
    return n;
  }

  function announce() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](state()); } catch (e) { /* never break the page */ }
    }
  }

  var blocked = false;
  function state() {
    if (blocked) { return "blocked"; }
    return pendingCount() ? "pending" : "clean";
  }

  function absorb(fields) {
    for (var namespace in fields) {
      for (var field in fields[namespace]) {
        put(prefix + ":" + namespace + ":" + field, fields[namespace][field].v);
      }
    }
    announce();
  }

  function flush() {
    if (!live || inflight) { return; }
    var q = queue();
    var sending = {}, count = 0, now = Date.now();
    for (var namespace in q) {
      sending[namespace] = {};
      for (var field in q[namespace]) {
        var entry = q[namespace][field];
        // An AGE, not a timestamp. The server subtracts it from its own clock,
        // so a flush that arrives late still lands where it happened.
        sending[namespace][field] = { v: entry.v, age_ms: now - entry.at };
        count += 1;
      }
    }
    if (!count) { return; }
    inflight = true;
    fetch(href, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields: sending }),
      keepalive: true,
      credentials: "same-origin"
    }).then(function (response) {
      if (response.status === 403 || response.status === 401) {
        // NOT offline. Keep the queue and say so — a silent 403 that eats marks
        // is the failure this whole design is trying not to have.
        blocked = true;
        throw new Error("sync blocked");
      }
      if (!response.ok) { throw new Error("sync " + response.status); }
      return response.json();
    }).then(function (payload) {
      blocked = false;
      // Clear ONLY what was sent: an edit made during the round trip is still
      // pending and must survive.
      var remaining = queue();
      for (var ns in sending) {
        for (var f in sending[ns]) {
          if (remaining[ns] && remaining[ns][f] &&
              remaining[ns][f].at === q[ns][f].at) {
            delete remaining[ns][f];
          }
        }
        if (remaining[ns] && !Object.keys(remaining[ns]).length) {
          delete remaining[ns];
        }
      }
      put(queueKey, remaining);
      if (payload && payload.fields) { absorb(payload.fields); }
      inflight = false;
      announce();
    }).catch(function () {
      inflight = false;
      announce();
      if (!retry) {
        retry = setTimeout(function () { retry = null; flush(); }, RETRY_MS);
      }
    });
  }

  function schedule() {
    if (timer) { clearTimeout(timer); }
    timer = setTimeout(function () { timer = null; flush(); }, FLUSH_MS);
  }

  if (live) {
    fetch(href, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (payload) {
        if (payload && payload.fields) { absorb(payload.fields); }
      })
      .catch(function () { /* offline: the cache is already rendered */ });
    window.addEventListener("online", flush);
    window.addEventListener("pagehide", flush);
  }

  return {
    get: function (namespace, field, fallback) {
      return raw(prefix + ":" + namespace + ":" + field, fallback);
    },
    set: function (namespace, field, value) {
      put(prefix + ":" + namespace + ":" + field, value);
      if (live) { enqueue(namespace, field, value); schedule(); }
    },
    pending: pendingCount,
    state: state,
    onchange: function (fn) { listeners.push(fn); }
  };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py -v`
Expected: PASS.

Also add `readview/*.js` coverage — check `pyproject.toml:51`'s
`package-data` already globs `readview/*.js`. It does; no change needed. Confirm
by reading the line rather than assuming.

- [ ] **Step 5: Commit**

```bash
git add vo_format/readview/sync.js tests/test_readview_state.py
git commit -m "Add the offline-first sync client with a persisted write queue"
```

---

### Task 6: Wire the read-view to it

**Files:**
- Modify: `vo_format/readview/render.py`, `vo_format/readview/cli.py`, `vo_format/readview/reader.js`, `vo_format/readview/reader.css`
- Test: `tests/test_readview_render.py`, `tests/test_readview_cli.py`

**Interfaces:**
- Consumes: `coldreadSync(prefix, href)` from Task 5.
- Produces: `render(script, library=None, sync=None)`; `--sync HREF` on `coldread-readview`.

- [ ] **Step 1: Write the failing tests**

```python
class TestSyncWiring:
    def test_sync_is_off_by_default(self) -> None:
        html = render(_script([_line("A line.")]))
        assert "coldreadSync" not in html
        assert "data-sync" not in html

    def test_sync_is_inlined_when_asked_for(self) -> None:
        html = render(_script([_line("A line.")]), sync="/state")
        assert "function coldreadSync(" in html
        assert 'data-sync="/state"' in html

    def test_the_sync_client_is_still_self_contained(self) -> None:
        html = render(_script([_line("A line.")]), sync="/state")
        assert "<script src" not in html

    def test_the_indicator_has_a_home(self) -> None:
        html = render(_script([_line("A line.")]), sync="/state")
        assert 'id="sync"' in html
```

And in `tests/test_readview_cli.py`, mirroring however the existing `--library`
test is written there:

```python
def test_the_sync_flag_reaches_render(tmp_path, monkeypatch):
    seen = {}

    def fake_render(script, library=None, sync=None):
        seen["sync"] = sync
        return "<html></html>"

    monkeypatch.setattr("vo_format.readview.cli.render", fake_render)
    pdf = _make_pdf(tmp_path)          # use the helper already in this file
    main([str(pdf), "--sync", "/state"])
    assert seen["sync"] == "/state"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_readview_render.py -k Sync tests/test_readview_cli.py -k sync -v`
Expected: FAIL — `render()` takes no `sync` argument.

- [ ] **Step 3: Thread the flag through `cli.py`**

Add the argument beside `--library`, matching its wording and its
justification:

```python
    parser.add_argument(
        "--sync",
        metavar="HREF",
        help=(
            "share read state with other devices via the state service at HREF "
            "(e.g. /state). Off by default: a lone read-view has no service to "
            "talk to, and no page should issue a request nobody asked for"
        ),
    )
```

Thread it through `_convert(pdf, force, library=None, sync=None)` into
`render(script, library=library, sync=sync)`, and through `main`'s call:
`_convert(pdf, force=args.force, library=args.library, sync=args.sync)`.

- [ ] **Step 4: Emit it from `render.py`**

Add the parameter and, mirroring `library_attr`:

```python
    sync_attr = f' data-sync="{escape(sync)}"' if sync else ""
    sync_js = f"<script>\n{_asset('sync.js')}\n</script>\n" if sync else ""
    sync_badge = '<span id="sync"></span>\n' if sync else ""
```

Put `{sync_attr}` on `<body>` beside `{library_attr}`, `{sync_badge}` inside
`#hud` immediately after `<span id="status"></span>`, and `{sync_js}` **before**
the `reader.js` `<script>` block so `coldreadSync` is defined when `reader.js`
runs.

- [ ] **Step 5: Use it in `reader.js`**

Replace the body of the existing `store` object so it delegates when sync is on,
keeping its exact `get`/`set` signatures so no call site changes:

```js
  var syncHref = body.dataset.sync || "";
  var shared = syncHref && typeof coldreadSync === "function"
    ? coldreadSync("coldread", syncHref)
    : null;
  var namespace = "script:" + (body.dataset.title || "untitled");

  var store = {
    get: function (key, fallback) {
      if (shared) { return shared.get(namespace, key, fallback); }
      try {
        var value = localStorage.getItem(storeKey + ":" + key);
        return value === null ? fallback : JSON.parse(value);
      } catch (e) { return fallback; }
    },
    set: function (key, value) {
      if (shared) { shared.set(namespace, key, value); return; }
      try { localStorage.setItem(storeKey + ":" + key, JSON.stringify(value)); }
      catch (e) { /* in-memory only */ }
    }
  };
```

Add the indicator, and the one rule that keeps sync from fighting the reader:

```js
  var touchedYet = false;
  if (shared) {
    var badge = document.getElementById("sync");
    shared.onchange(function (state) {
      if (!badge) { return; }
      badge.textContent = state === "clean" ? "" : "⇅";
      badge.className = state;
    });
  }
```

Set `touchedYet = true` at the top of the `touchstart` and `mousedown` handlers,
and guard the load-time seek: **`pos` and `mark` arriving from the server are
applied only if nothing has been touched yet.** A server scroll position yanking
the script mid-read is worse than not syncing it.

- [ ] **Step 6: Style the indicator**

In `reader.css`, beside the `#status` rule:

```css
/* Silence means clean. Amber means the queue has something in it; red means the
   server refused us, which is emphatically not the same as being offline. */
#sync { min-width: 1.5ch; text-align: center; opacity: 0.9; }
#sync.pending { color: #ffb84d; }
#sync.blocked { color: #ff6b6b; }
```

- [ ] **Step 7: Run the suite**

Run: `./.venv/bin/python -m pytest tests/ -q` → pass.
Run: `./.venv/bin/ruff check vo_format tests` → clean.

- [ ] **Step 8: Commit**

```bash
git add vo_format/readview/render.py vo_format/readview/cli.py \
        vo_format/readview/reader.js vo_format/readview/reader.css \
        tests/test_readview_render.py tests/test_readview_cli.py
git commit -m "Wire the read-view to shared state behind an opt-in --sync flag"
```

---

### Task 7: Wire the library page, and externalise the channels

**Files:**
- Modify: `vo_format/readview/library.py`
- Test: `tests/test_readview_library.py`

**Interfaces:**
- Consumes: `sync.js` from Task 5.
- Produces: `read_channel_config(directory) -> tuple[tuple[str, ...], dict]`; `render_index(entries, order=None, labels=None, sync=None)`.

- [ ] **Step 1: Write the failing tests**

```python
class TestSyncParity:
    def test_the_embedded_client_matches_the_asset_byte_for_byte(self) -> None:
        # library.py is piped to a remote interpreter, so it has no __file__ and
        # cannot read sync.js. The copy is unavoidable; the drift is not.
        from importlib.resources import files

        from vo_format.readview.library import _SYNC_JS

        canonical = (files("vo_format.readview") / "sync.js").read_text(
            encoding="utf-8"
        )
        assert _SYNC_JS == canonical


class TestChannelConfig:
    def test_an_absent_config_keeps_todays_behaviour(self, tmp_path) -> None:
        from vo_format.readview.library import read_channel_config

        order, labels = read_channel_config(tmp_path)
        assert order == ()
        assert labels == {}

    def test_a_config_is_honoured(self, tmp_path) -> None:
        import json

        from vo_format.readview.library import read_channel_config

        (tmp_path / "channels.json").write_text(
            json.dumps({"order": ["B", "A"], "labels": {"A": "Ay"}}),
            encoding="utf-8",
        )
        order, labels = read_channel_config(tmp_path)
        assert order == ("B", "A")
        assert labels == {"A": "Ay"}

    def test_a_malformed_config_falls_back_rather_than_raising(
        self, tmp_path
    ) -> None:
        from vo_format.readview.library import read_channel_config

        (tmp_path / "channels.json").write_text("{oops", encoding="utf-8")
        assert read_channel_config(tmp_path) == ((), {})

    def test_no_channel_name_is_hardcoded_any_more(self) -> None:
        from vo_format.readview import library

        source = (
            __import__("pathlib").Path(library.__file__).read_text(encoding="utf-8")
        )
        assert "CassetteLore" not in source
        assert "Birds of Play" not in source
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_readview_library.py -k "Parity or ChannelConfig" -v`
Expected: FAIL on the missing names, and `test_no_channel_name_is_hardcoded_any_more` fails against the current literals at `library.py:32-35`.

- [ ] **Step 3: Externalise the channel config**

Delete `CHANNEL_LABELS` and `CHANNEL_ORDER` (`library.py:32-35`). Add:

```python
#: Optional per-deployment config, read from the library directory so it travels
#: with the content on the same rsync. Absent or malformed means "no opinion":
#: channels then show their raw filename prefix in alphabetical order, which is
#: what this module already did correctly for an unlisted channel.
_CONFIG_NAME = "channels.json"


def read_channel_config(directory) -> tuple[tuple[str, ...], dict]:
    try:
        raw = (pathlib.Path(directory) / _CONFIG_NAME).read_text(encoding="utf-8")
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
```

Add `import json` to the imports. Thread `order` and `labels` through `_grouped`,
`_section` and `render_index` as parameters rather than module globals, and have
`main()` call `read_channel_config(root)` and pass the result down.

- [ ] **Step 4: Embed the sync client and use it**

Add `_SYNC_JS = r"""…"""` holding the byte-identical contents of `sync.js`, kept
out of any f-string. Then in `_JS`, replace the `store` object's two methods so
they delegate to `coldreadSync("coldread-library", href)` when
`document.body.dataset.sync` is set, using namespaces `read` and `open`, exactly
mirroring Task 6's shape. Emit `data-sync` on `<body>` when `render_index` is
given a `sync` href, and extend the `#loaded` line to append `· synced`,
`· N pending`, or `· sync blocked`.

- [ ] **Step 5: Run the suite**

Run: `./.venv/bin/python -m pytest tests/ -q` → pass.
Run: `./.venv/bin/ruff check vo_format tests` → clean.
Run the stdlib-only assertion specifically, since this task added an import:
`./.venv/bin/python -m pytest tests/test_readview_library.py -k stdlib -v`

- [ ] **Step 6: Commit**

```bash
git add vo_format/readview/library.py tests/test_readview_library.py
git commit -m "Share library read state, and take the channel names out of the code"
```

---

### Task 8: The stdlib-only guard for `state.py`, and the docs

**Files:**
- Modify: `tests/test_readview_state.py`, `README.md`

- [ ] **Step 1: Write the failing test**

Mirror `test_readview_library.py::test_module_imports_only_the_stdlib` — read it
first and follow its shape rather than inventing a second style:

```python
class TestSelfContainment:
    """It runs on the serving host with nothing installed."""

    def test_it_imports_only_the_stdlib(self) -> None:
        import pathlib
        import re
        import sys

        from vo_format.readview import state

        source = pathlib.Path(state.__file__).read_text(encoding="utf-8")
        names = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, re.M)
        assert names, "no imports found - did the file move?"
        for name in names:
            root = name.split(".")[0]
            assert root in sys.stdlib_module_names, f"{name} is not stdlib"
            assert root != "vo_format", "state.py must not import the package"
```

- [ ] **Step 2: Run it**

Run: `./.venv/bin/python -m pytest tests/test_readview_state.py -k stdlib -v`
Expected: PASS if Tasks 1-4 stayed inside the stdlib. If it fails, the import
that broke it is the bug — remove it, do not relax the test.

- [ ] **Step 3: Document it in the README**

Add the `coldread-state` invocation and what it is for to the read-view section.
Keep it generic: no hostname, no IP, no personal directory. Note that it is
opt-in, that the read-view works without it, and that it wants to sit on the same
origin as the library so the page's fetch is same-origin.

This overlaps issue #144's section. Coordinate: #144 documents the library and
the hosting requirements, this documents the state service. Land whichever is
second on top of the first rather than in parallel.

- [ ] **Step 4: Commit**

```bash
git add tests/test_readview_state.py README.md
git commit -m "Guard state.py's stdlib-only property and document coldread-state"
```

---

## Human review gate

Not any task's steps — these need two real devices and cannot be done from a
worktree.

- [ ] Mark a script complete on the phone; confirm the desktop shows it complete
      after a reload.
- [ ] Set a resume mark on the desktop; confirm the phone opens at it.
- [ ] **The offline round trip.** Put the tablet in airplane mode, mark two
      scripts complete and set a mark, close the page, reconnect, reopen.
      Confirm all three arrive — and confirm nothing the desktop did meanwhile
      was lost.
- [ ] **The late-flush case.** Offline on device A, mark a script complete. Then
      on device B mark the *same* script incomplete. Reconnect A. B's newer
      action must win, because A's was older despite arriving later. This is the
      one behaviour that no test can prove is wired correctly end to end.
- [ ] Break the token deliberately and confirm the indicator goes red and stays
      red, and that nothing queued is lost.

---

## Self-review

**Spec coverage.** Field-level LWW — Task 1. Tombstones — Task 1. Age-based
back-dating and the monotonic base — Task 1. Atomic save, backup, tolerant load —
Task 2. Token, cookie, origin, 405, log scrubbing, body cap — Task 3. Loud
refusal to start — Task 4. Persisted queue, `keepalive`, `online`, retry, 403 ≠
offline, inert off http — Task 5. `--sync` opt-in, indicator, the "don't apply
`pos`/`mark` after first touch" rule — Task 6. Parity test, `channels.json` —
Task 7. Stdlib guard — Task 8. No pruning, as specified.

**Placeholders.** Tasks 1-6 carry literal code. Task 7 Step 4 and Task 8 Step 3
describe edits in prose rather than showing every line — they are mechanical
mirrors of Task 6's shape, and `_SYNC_JS` is a copy of a file that exists by
then. Flagged rather than hidden: an implementer who wants literal text for those
should read Task 6 Step 5 and mirror it.

**Type consistency.** `coldreadSync(prefix, href)` returns `{get, set, pending,
state, onchange}`, used identically in Tasks 5, 6 and 7. `render(script,
library=None, sync=None)` matches between `cli.py`, `render.py` and the tests.
`read_channel_config` returns `(order_tuple, labels_dict)` in both its test and
its implementation.
