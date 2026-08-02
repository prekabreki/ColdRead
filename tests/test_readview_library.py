"""The library index groups read-views by channel and survives being piped.

This page is the only navigation the iPad has, so an entry that fails to appear
is a script that cannot be reached. The tests therefore lean on presence and
grouping rather than markup shape.

The channel names below are a *fixture*, not a default: `library.py` no longer
knows any, and reads them from a `channels.json` in the directory it indexes.
Every test that cares about labelling or ordering therefore states its config,
which is also how a reader can tell which behaviour is configured and which is
the fallback.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from html import escape
from importlib.resources import files
from pathlib import Path
from typing import Sequence

import pytest

from vo_format.readview.library import (
    _PREFIX_BYTES,
    _SYNC_JS,
    IndexEntry,
    main,
    read_channel_config,
    render_index,
    words_in,
)

LIBRARY_PY = (
    Path(__file__).resolve().parents[1] / "vo_format" / "readview" / "library.py"
)

#: A deployment's channel opinions, as `channels.json` would carry them.
ORDER = ("CassetteLore", "Birds of Play")
LABELS = {"CassetteLore": "Cassette Lore"}
CONFIG = {"order": list(ORDER), "labels": LABELS}


def _render(entries: Sequence[IndexEntry], **kwargs) -> str:
    """`render_index` with the fixture config, unless a test overrides it."""
    kwargs.setdefault("order", ORDER)
    kwargs.setdefault("labels", LABELS)
    return render_index(entries, **kwargs)


def _write_config(directory: Path, config: object = CONFIG) -> None:
    (directory / "channels.json").write_text(
        json.dumps(config), encoding="utf-8"
    )


def _entry(
    channel: str, title: str, date: str = "2026-07-31", words: int | None = None
) -> IndexEntry:
    return IndexEntry(
        channel=channel,
        title=title,
        filename=f"{channel} — {title} - readview.html",
        date=date,
        words=words,
    )


def _readview(words: int | None) -> str:
    """A read-view's opening bytes, as `render.py` writes them."""
    attr = "" if words is None else f' data-words="{words}"'
    return (
        '<!doctype html>\n<html lang="en" data-theme="dark">\n<head>\n'
        "<title>A v1</title>\n<style>body{margin:0}</style>\n</head>\n"
        f'<body data-words-per-line="7.0" data-title="A v1"{attr}>\n'
        '<div id="script"></div>\n</body></html>\n'
    )


REAL_ENTRIES = [
    _entry("CassetteLore", "Warcraft Episode 1 The Fall of Quel'Thalas v8 - formatted"),
    _entry("CassetteLore", "Warhammer 40K Episode 4 The Burning v6 - batched"),
    _entry(
        "Birds of Play", "The Complete Story of Kingdom Hearts Dark Road - formatted"
    ),
]


def _section(html: str, label: str) -> str:
    """The <details> block whose summary carries `label`."""
    blocks = re.findall(r"<details.*?</details>", html, re.S)
    matching = [b for b in blocks if label in b]
    assert matching, f"no <details> section for {label!r}"
    assert len(matching) == 1, f"{label!r} appears in {len(matching)} sections"
    return matching[0]


class TestGrouping:
    def test_one_section_per_channel(self):
        html = _render(REAL_ENTRIES)
        assert len(re.findall(r"<details", html)) == 2

    def test_entries_land_in_their_own_channel(self):
        html = _render(REAL_ENTRIES)
        cl = _section(html, "Cassette Lore")
        bop = _section(html, "Birds of Play")
        assert "Warcraft Episode 1" in cl
        assert "Warhammer 40K" in cl
        assert "Kingdom Hearts Dark Road" in bop
        assert "Kingdom Hearts" not in cl

    def test_every_entry_appears_exactly_once(self):
        html = _render(REAL_ENTRIES)
        for entry in REAL_ENTRIES:
            assert html.count(f'href="{escape(entry.filename, quote=True)}"') == 1

    def test_a_configured_label_replaces_the_raw_prefix(self):
        html = _render(REAL_ENTRIES)
        assert "Cassette Lore" in html
        # The raw filename channel must not reach the display label.
        assert ">CassetteLore" not in html

    def test_configured_order_is_honoured(self):
        html = _render(REAL_ENTRIES)
        assert html.index("Cassette Lore") < html.index("Birds of Play")

    def test_unknown_channel_passes_through_and_sorts_last(self):
        # The behaviour that keeps a brand-new channel reachable the moment its
        # first script lands, before anyone has touched channels.json.
        html = _render([*REAL_ENTRIES, _entry("Some New Show", "Pilot - formatted")])
        assert len(re.findall(r"<details", html)) == 3
        assert "Some New Show" in html
        assert html.index("Birds of Play") < html.index("Some New Show")
        assert "Pilot" in _section(html, "Some New Show")

    def test_empty_index_renders_without_sections(self):
        html = _render([])
        assert "<details" not in html
        assert "<html" in html and "</html>" in html


class TestWithoutConfig:
    """No `channels.json`: raw prefixes, alphabetical, nothing dropped."""

    def test_every_channel_still_gets_a_section(self):
        html = render_index(REAL_ENTRIES)
        assert len(re.findall(r"<details", html)) == 2
        for entry in REAL_ENTRIES:
            assert f'href="{escape(entry.filename, quote=True)}"' in html

    def test_the_raw_prefix_is_the_label(self):
        html = render_index(REAL_ENTRIES)
        assert "CassetteLore" in _section(html, "CassetteLore")
        assert "Cassette Lore" not in html

    def test_channels_fall_back_to_alphabetical_order(self):
        html = render_index(REAL_ENTRIES)
        assert html.index("Birds of Play") < html.index("CassetteLore")

    def test_labels_without_an_order_still_apply(self):
        # The two halves of the config are independent; a deployment that only
        # wants nicer names should not have to enumerate every channel it has.
        html = render_index(REAL_ENTRIES, labels=LABELS)
        assert "Cassette Lore" in html
        assert html.index("Birds of Play") < html.index("Cassette Lore")


class TestSummaryCounts:
    """Both sections start closed, so the summary carries what the body hides."""

    def test_summary_states_the_script_count(self):
        html = _render(REAL_ENTRIES)
        assert "2 scripts" in _section(html, "Cassette Lore")
        assert "1 script" in _section(html, "Birds of Play")

    def test_singular_script_is_not_pluralised(self):
        bop = _section(_render(REAL_ENTRIES), "Birds of Play")
        assert "1 scripts" not in bop

    def test_sections_are_closed_by_default(self):
        html = _render(REAL_ENTRIES)
        for block in re.findall(r"<details[^>]*>", html):
            assert "open" not in block

    def test_read_count_element_exists_per_section(self):
        # The count itself is filled in by JS from localStorage; the element it
        # writes into must be present in the served HTML, per channel.
        html = _render(REAL_ENTRIES)
        assert 'class="readcount"' in _section(html, "Cassette Lore")
        assert 'class="readcount"' in _section(html, "Birds of Play")


class TestEscaping:
    def test_apostrophe_in_a_title_is_escaped(self):
        html = _render(REAL_ENTRIES)
        assert "Quel&#x27;Thalas" in html
        assert "Quel'Thalas" not in html

    def test_angle_brackets_in_a_title_cannot_inject_markup(self):
        html = _render([_entry("CassetteLore", "<script>alert(1)</script>")])
        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;" in html

    def test_ampersand_in_a_filename_is_escaped_in_the_href(self):
        entry = IndexEntry(
            channel="CassetteLore",
            title="Fire & Blood - formatted",
            filename="CassetteLore — Fire & Blood - formatted - readview.html",
            date="2026-07-31",
        )
        html = _render([entry])
        assert "Fire &amp; Blood" in html
        assert 'href="CassetteLore — Fire & Blood' not in html

    def test_a_channel_label_cannot_inject_markup(self):
        # channels.json is hand-edited, and now reaches the page unchanged.
        html = _render([_entry("X", "Thing - formatted")], labels={"X": "<b>oops"})
        assert "<b>oops" not in html
        assert "&lt;b&gt;oops" in html

    def test_a_sync_href_cannot_break_out_of_its_attribute(self):
        html = _render([], sync='/state" onload="x')
        assert 'onload="x"' not in html
        assert "&quot;" in html


class TestRowsCarryWhatTheHandlerNeeds:
    def test_each_row_is_keyed_by_its_filename(self):
        html = _render(REAL_ENTRIES)
        # The swipe handler persists read state under the filename, which carries
        # the draft version - that is what makes a new draft come back unread.
        for entry in REAL_ENTRIES:
            assert f'data-key="{escape(entry.filename, quote=True)}"' in html

    def test_each_section_is_keyed_by_its_raw_channel(self):
        html = _render(REAL_ENTRIES)
        assert 'data-channel="CassetteLore"' in html
        assert 'data-channel="Birds of Play"' in html

    def test_rows_carry_their_position_within_the_channel(self):
        # Unmarking a script puts it back in title order rather than leaving it
        # at the bottom, which needs the original position on each row.
        html = _render(
            [
                _entry("CassetteLore", "Bravo - formatted"),
                _entry("CassetteLore", "Alpha - formatted"),
                _entry("Birds of Play", "Solo - formatted"),
            ]
        )
        cl = _section(html, "Cassette Lore")
        assert re.findall(r'data-i="(\d+)"', cl) == ["0", "1"]
        # Sorted by title, so Alpha is position 0 even though it was listed last.
        assert cl.index('data-i="0"') < cl.index("Bravo")
        assert re.findall(r'data-i="(\d+)"', _section(html, "Birds of Play")) == ["0"]

    def test_every_row_has_a_toggle_target(self):
        html = _render(REAL_ENTRIES)
        assert len(re.findall(r'class="check"', html)) == len(REAL_ENTRIES)

    def test_the_date_is_shown(self):
        html = _render([_entry("CassetteLore", "Thing - formatted", date="2026-01-09")])
        assert "2026-01-09" in html


class TestLength:
    """Length is what decides whether a script fits the time available."""

    def test_word_count_is_shown_with_thousands_separators(self):
        html = _render([_entry("CassetteLore", "Thing - formatted", words=1340)])
        assert "1,340 words" in html

    def test_length_leads_the_sub_line_ahead_of_the_date(self):
        html = _render(
            [_entry("CassetteLore", "Thing - formatted", date="2026-01-09", words=1340)]
        )
        # Both facts on one line, length first. Asserting order rather than mere
        # presence: a row that rendered them in two places would still pass a
        # containment check.
        assert "1,340 words &middot; 2026-01-09" in html

    def test_a_missing_count_degrades_to_the_date_alone(self):
        html = _render(
            [_entry("CassetteLore", "Thing - formatted", date="2026-01-09", words=None)]
        )
        assert "<span>2026-01-09</span>" in html
        assert "words" not in html
        # And the row is still reachable, which is the whole point of degrading.
        assert "<b>Thing</b>" in html

    def test_a_zero_count_is_shown_rather_than_treated_as_missing(self):
        # 0 is falsy. A read-view that really did extract no words should say so,
        # not silently look like an older file that carries no count at all.
        html = _render([_entry("CassetteLore", "Empty - formatted", words=0)])
        assert "0 words" in html


class TestWordsIn:
    def test_reads_the_count_render_stamped_on_the_body(self, tmp_path: Path):
        path = tmp_path / "a - readview.html"
        path.write_text(_readview(1340), encoding="utf-8")
        assert words_in(path) == 1340

    def test_an_older_readview_without_the_attribute_yields_none(self, tmp_path: Path):
        path = tmp_path / "a - readview.html"
        path.write_text(_readview(None), encoding="utf-8")
        assert words_in(path) is None

    def test_a_non_numeric_attribute_yields_none(self, tmp_path: Path):
        path = tmp_path / "a - readview.html"
        path.write_text(
            _readview(None).replace("data-title", 'data-words="lots" data-title')
        )
        assert words_in(path) is None

    def test_a_file_that_is_not_a_readview_yields_none(self, tmp_path: Path):
        path = tmp_path / "a - readview.html"
        path.write_text("<html><body>nothing here</body></html>", encoding="utf-8")
        assert words_in(path) is None

    def test_an_unreadable_file_yields_none_rather_than_raising(self, tmp_path: Path):
        # The index must render even if one read-view cannot be opened; a
        # directory standing in for a file is the cheapest way to force that.
        assert words_in(tmp_path / "not-a-file") is None
        (tmp_path / "dir - readview.html").mkdir()
        assert words_in(tmp_path / "dir - readview.html") is None

    def test_undecodable_bytes_do_not_raise(self, tmp_path: Path):
        # A prefix read can slice a multi-byte character in half. Errors are
        # replaced, not raised, and the count on the same line still comes back.
        path = tmp_path / "a - readview.html"
        path.write_bytes(b'<body data-title="\xff\xfe" data-words="42">')
        assert words_in(path) == 42

    def test_only_a_bounded_prefix_is_read(self, tmp_path: Path):
        # The read is bounded so the index pass over a whole directory does not
        # depend on pulling every script's body into memory on the Pi. A count
        # buried past the bound is missed; that is the accepted trade.
        #
        # The padding is DERIVED from the bound, not a number that happens to
        # exceed today's value - hard-coding it made this test pass for the wrong
        # reason the moment the bound was raised.
        path = tmp_path / "a - readview.html"
        padding = "x" * (_PREFIX_BYTES + 512)
        path.write_text(f"<!-- {padding} -->" + _readview(1340), encoding="utf-8")
        assert words_in(path) is None


class TestChannelConfig:
    """`channels.json` is hand-edited on a Pi, so it must never be fatal."""

    def test_an_absent_config_means_no_opinion(self, tmp_path: Path):
        assert read_channel_config(tmp_path) == ((), {})

    def test_a_config_is_honoured(self, tmp_path: Path):
        _write_config(tmp_path, {"order": ["B", "A"], "labels": {"A": "Ay"}})
        order, labels = read_channel_config(tmp_path)
        assert order == ("B", "A")
        assert labels == {"A": "Ay"}

    def test_a_malformed_config_falls_back_rather_than_raising(self, tmp_path: Path):
        (tmp_path / "channels.json").write_text("{oops", encoding="utf-8")
        assert read_channel_config(tmp_path) == ((), {})

    def test_a_config_that_is_not_an_object_falls_back(self, tmp_path: Path):
        # Valid JSON of the wrong shape: `["A", "B"]` is the mistake someone
        # writes when they remember only the order half of the file.
        _write_config(tmp_path, ["A", "B"])
        assert read_channel_config(tmp_path) == ((), {})

    def test_the_wrong_type_for_one_half_does_not_lose_the_other(
        self, tmp_path: Path
    ):
        _write_config(tmp_path, {"order": "CassetteLore", "labels": {"A": "Ay"}})
        assert read_channel_config(tmp_path) == ((), {"A": "Ay"})
        _write_config(tmp_path, {"order": ["A"], "labels": ["A"]})
        assert read_channel_config(tmp_path) == (("A",), {})

    def test_non_string_entries_are_coerced_rather_than_crashing(self, tmp_path: Path):
        _write_config(tmp_path, {"order": [1, "A"], "labels": {"A": 2}})
        assert read_channel_config(tmp_path) == (("1", "A"), {"A": "2"})

    def test_a_directory_that_does_not_exist_is_not_fatal(self, tmp_path: Path):
        assert read_channel_config(tmp_path / "nope") == ((), {})

    def test_no_channel_name_is_hardcoded_any_more(self):
        # The names belong to a deployment, not to this public repo.
        source = LIBRARY_PY.read_text(encoding="utf-8")
        assert "CassetteLore" not in source
        assert "Birds of Play" not in source


class TestSyncParity:
    def test_the_embedded_client_matches_the_asset_byte_for_byte(self):
        # library.py is piped to a remote interpreter, so it has no __file__ and
        # cannot read sync.js. The copy is unavoidable; the drift is not.
        #
        # Read as bytes and normalise the line endings on both sides. The repo
        # stores sync.js with LF, but a Windows checkout under
        # `core.autocrlf=true` writes CRLF, while Python's tokenizer always
        # normalises the literal in library.py to LF. Comparing raw would fail
        # on a platform difference that has nothing to do with drift.
        canonical = (
            (files("vo_format.readview") / "sync.js")
            .read_bytes()
            .decode("utf-8")
            .replace("\r\n", "\n")
        )
        assert "function coldreadSync(" in canonical
        assert _SYNC_JS.replace("\r\n", "\n") == canonical

    def test_the_copy_is_not_wrapped_or_re_indented(self):
        # A literal that merely *contains* the client would pass a containment
        # check while the page shipped something subtly re-formatted.
        assert _SYNC_JS.startswith('"use strict";')
        assert _SYNC_JS.endswith("}\n")


class TestSyncWiring:
    def test_sync_is_off_by_default(self):
        # The page script always *feature-detects* coldreadSync, so what has to
        # be absent is the client itself and the attribute that switches it on.
        # A page carrying neither cannot issue a request nobody asked for.
        html = _render(REAL_ENTRIES)
        assert "function coldreadSync(" not in html
        assert "data-sync" not in html
        assert "localStorage" in html  # ...and still remembers what was read

    def test_sync_is_inlined_when_asked_for(self):
        html = _render(REAL_ENTRIES, sync="/state")
        assert "function coldreadSync(" in html
        assert 'data-sync="/state"' in html

    def test_the_client_is_defined_before_the_page_script_runs(self):
        html = _render(REAL_ENTRIES, sync="/state")
        assert html.index("function coldreadSync(") < html.index(
            'var PREFIX = "coldread-library"'
        )

    def test_the_page_stays_self_contained_with_sync_on(self):
        html = _render(REAL_ENTRIES, sync="/state")
        assert "<script src" not in html
        assert "http://" not in html
        assert "https://" not in html

    def test_read_state_is_stored_per_script_not_as_one_blob(self):
        # Field-level state is what makes two devices marking two different
        # scripts while offline both survive the merge.
        html = _render(REAL_ENTRIES, sync="/state")
        assert 'store.set("read", li.dataset.key' in html
        assert 'store.get("read", li.dataset.key' in html

    def test_the_loaded_line_reports_the_sync_state(self):
        html = _render(REAL_ENTRIES, sync="/state")
        assert "sync blocked" in html
        assert "pending" in html
        assert "synced" in html


class TestSelfContainment:
    """It runs as `python3 -` on the Pi with nothing installed."""

    def test_module_imports_only_the_stdlib(self):
        source = LIBRARY_PY.read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, re.M)
        assert imports, "no imports found - did the file move?"
        for name in imports:
            root = name.split(".")[0]
            assert root != "vo_format", (
                f"relative/package import would break piping: {name}"
            )
            assert root in sys.stdlib_module_names, f"non-stdlib import: {name}"

    def test_page_references_no_external_host(self):
        html = _render(REAL_ENTRIES)
        assert "http://" not in html
        assert "https://" not in html

    def test_runs_as_a_piped_script(self, tmp_path: Path):
        for entry in REAL_ENTRIES:
            (tmp_path / entry.filename).write_text("<html></html>", encoding="utf-8")
        _write_config(tmp_path)
        result = subprocess.run(
            [sys.executable, "-", str(tmp_path)],
            input=LIBRARY_PY.read_text(encoding="utf-8"),
            capture_output=True,
            text=True,
            # library.py is UTF-8 and contains non-Latin-1 characters. Without
            # this, the pipe is encoded with the machine's locale codec (cp1252
            # on Windows) and the test fails for reasons unrelated to the code.
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        # The label proves channels.json travelled with the content and was read
        # on the far end, which is the whole reason the names left the code.
        assert "Cassette Lore" in html
        assert "Warcraft Episode 1" in html

    def test_a_piped_run_takes_the_sync_href_as_a_second_argument(
        self, tmp_path: Path
    ):
        # Without this a deployment could never switch sharing on: library.py is
        # invoked as `python3 - <dir>` and has no other way in.
        (tmp_path / "loose - readview.html").write_text("x", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-", str(tmp_path), "/state"],
            input=LIBRARY_PY.read_text(encoding="utf-8"),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stderr
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert 'data-sync="/state"' in html

    def test_too_many_arguments_is_a_usage_error(self, tmp_path: Path):
        result = subprocess.run(
            [sys.executable, "-", str(tmp_path), "/state", "extra"],
            input=LIBRARY_PY.read_text(encoding="utf-8"),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 64
        assert "usage:" in result.stderr


class TestMain:
    def test_glob_skips_index_itself(self, tmp_path: Path):
        (tmp_path / "CassetteLore — A v1 - formatted - readview.html").write_text("x")
        (tmp_path / "index.html").write_text("stale")
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "1 script" in html
        assert "index.html</b>" not in html

    def test_filename_without_the_channel_separator_is_kept(self, tmp_path: Path):
        # A read-view pushed by hand has no "Channel — " prefix. It must still be
        # reachable rather than silently dropped off the only navigation there is.
        (tmp_path / "loose-script - readview.html").write_text("x")
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        # Asserting on the display title, not merely on the string appearing
        # somewhere: the href alone would satisfy that while the row rendered
        # blank.
        assert "<b>loose-script</b>" in html

    def test_formatted_variant_is_suppressed_in_display_title(
        self, tmp_path: Path
    ):
        (tmp_path / "CassetteLore — A v1 - formatted - readview.html").write_text("x")
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "<b>A v1</b>" in html
        # Still present in data-key and href so read state survives.
        assert 'data-key="CassetteLore — A v1 - formatted - readview.html"' in html

    def test_batched_variant_is_kept_in_display_title(self, tmp_path: Path):
        (tmp_path / "CassetteLore — A v1 - batched - readview.html").write_text("x")
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "<b>A v1 - batched</b>" in html

    def test_readview_suffix_is_stripped_from_the_display_title(self, tmp_path: Path):
        (tmp_path / "CassetteLore — A v1 - formatted - readview.html").write_text("x")
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "readview.html</b>" not in html
        assert "<b>A v1</b>" in html

    def test_the_config_is_read_from_the_directory_being_indexed(self, tmp_path: Path):
        (tmp_path / "CassetteLore — A v1 - formatted - readview.html").write_text("x")
        _write_config(tmp_path)
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "Cassette Lore" in html

    def test_a_broken_config_costs_the_label_and_nothing_else(self, tmp_path: Path):
        (tmp_path / "CassetteLore — A v1 - formatted - readview.html").write_text("x")
        (tmp_path / "channels.json").write_text("{oops", encoding="utf-8")
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "<b>A v1</b>" in html
        assert 'data-channel="CassetteLore"' in html

    def test_the_config_is_not_mistaken_for_a_read_view(self, tmp_path: Path):
        _write_config(tmp_path)
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "channels.json" not in html
        assert "0 scripts" in html

    def test_sync_is_off_unless_main_is_given_a_href(self, tmp_path: Path):
        (tmp_path / "loose - readview.html").write_text("x")
        main(str(tmp_path))
        assert "data-sync" not in (tmp_path / "index.html").read_text(encoding="utf-8")
        main(str(tmp_path), sync="/state")
        assert 'data-sync="/state"' in (tmp_path / "index.html").read_text(
            encoding="utf-8"
        )

    def test_data_key_stays_full_filename_when_formatted_stripped(
        self, tmp_path: Path
    ):
        (tmp_path / "CassetteLore — A v1 - formatted - readview.html").write_text("x")
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "<b>A v1</b>" in html
        assert (
            'data-key="CassetteLore — A v1 - formatted - readview.html"' in html
        )

    def test_mid_title_formatted_word_is_left_alone(self, tmp_path: Path):
        (tmp_path / "CassetteLore — The formatted file v1 - readview.html").write_text(
            "x"
        )
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "<b>The formatted file v1</b>" in html


class TestLengthEndToEnd:
    def test_main_shows_the_count_from_the_files_on_disk(self, tmp_path: Path):
        (tmp_path / "CassetteLore — A v1 - formatted - readview.html").write_text(
            _readview(2500), encoding="utf-8"
        )
        (tmp_path / "CassetteLore — B v1 - formatted - readview.html").write_text(
            _readview(None), encoding="utf-8"
        )
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "2,500 words" in html
        # The countless one still gets a row rather than disappearing.
        assert "<b>B v1</b>" in html
        assert html.count("words") == 1
