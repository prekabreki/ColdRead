"""The merge rules. Every one of these encodes a way state can silently vanish."""

from __future__ import annotations

import json
import pathlib

import pytest

from vo_format.readview.state import (
    MAX_AGE_MS,
    Clock,
    Store,
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
        # A backwards reading is bumped one ms past the last; a reading that has
        # caught up again is honoured as-is, per the spec's
        # `now = max(clock_now, last_now + 1)`.
        clock = Clock(source=iter([1000, 900, 900, 1005]).__next__)
        assert [clock.now() for _ in range(4)] == [1000, 1001, 1002, 1005]

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
            {"read": {"a.html": {"v": True, "age_ms": 500}}},  # happened at 500
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
