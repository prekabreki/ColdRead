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

import json
import os
import pathlib
import sys
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
