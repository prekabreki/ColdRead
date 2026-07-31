"""The library index groups read-views by channel and survives being piped.

This page is the only navigation the iPad has, so an entry that fails to appear
is a script that cannot be reached. The tests therefore lean on presence and
grouping rather than markup shape.
"""

from __future__ import annotations

import re
import subprocess
import sys
from html import escape
from pathlib import Path

import pytest

from vo_format.readview.library import (
    _PREFIX_BYTES,
    IndexEntry,
    main,
    render_index,
    words_in,
)

LIBRARY_PY = Path(__file__).resolve().parents[1] / "vo_format" / "readview" / "library.py"


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
    _entry("Birds of Play", "The Complete Story of Kingdom Hearts Dark Road - formatted"),
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
        html = render_index(REAL_ENTRIES)
        assert len(re.findall(r"<details", html)) == 2

    def test_entries_land_in_their_own_channel(self):
        html = render_index(REAL_ENTRIES)
        cl = _section(html, "Cassette Lore")
        bop = _section(html, "Birds of Play")
        assert "Warcraft Episode 1" in cl
        assert "Warhammer 40K" in cl
        assert "Kingdom Hearts Dark Road" in bop
        assert "Kingdom Hearts" not in cl

    def test_every_entry_appears_exactly_once(self):
        html = render_index(REAL_ENTRIES)
        for entry in REAL_ENTRIES:
            assert html.count(f'href="{escape(entry.filename, quote=True)}"') == 1

    def test_cassette_lore_is_labelled_with_a_space(self):
        html = render_index(REAL_ENTRIES)
        assert "Cassette Lore" in html
        # The raw filename channel must not reach the display label.
        assert ">CassetteLore" not in html

    def test_cassette_lore_precedes_birds_of_play(self):
        html = render_index(REAL_ENTRIES)
        assert html.index("Cassette Lore") < html.index("Birds of Play")

    def test_unknown_channel_passes_through_and_sorts_last(self):
        html = render_index([*REAL_ENTRIES, _entry("Some New Show", "Pilot - formatted")])
        assert len(re.findall(r"<details", html)) == 3
        assert "Some New Show" in html
        assert html.index("Birds of Play") < html.index("Some New Show")
        assert "Pilot" in _section(html, "Some New Show")

    def test_empty_index_renders_without_sections(self):
        html = render_index([])
        assert "<details" not in html
        assert "<html" in html and "</html>" in html


class TestSummaryCounts:
    """Both sections start closed, so the summary carries what the body hides."""

    def test_summary_states_the_script_count(self):
        html = render_index(REAL_ENTRIES)
        assert "2 scripts" in _section(html, "Cassette Lore")
        assert "1 script" in _section(html, "Birds of Play")

    def test_singular_script_is_not_pluralised(self):
        bop = _section(render_index(REAL_ENTRIES), "Birds of Play")
        assert "1 scripts" not in bop

    def test_sections_are_closed_by_default(self):
        html = render_index(REAL_ENTRIES)
        for block in re.findall(r"<details[^>]*>", html):
            assert "open" not in block

    def test_read_count_element_exists_per_section(self):
        # The count itself is filled in by JS from localStorage; the element it
        # writes into must be present in the served HTML, per channel.
        html = render_index(REAL_ENTRIES)
        assert 'class="readcount"' in _section(html, "Cassette Lore")
        assert 'class="readcount"' in _section(html, "Birds of Play")


class TestEscaping:
    def test_apostrophe_in_a_title_is_escaped(self):
        html = render_index(REAL_ENTRIES)
        assert "Quel&#x27;Thalas" in html
        assert "Quel'Thalas" not in html

    def test_angle_brackets_in_a_title_cannot_inject_markup(self):
        html = render_index([_entry("CassetteLore", "<script>alert(1)</script>")])
        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;" in html

    def test_ampersand_in_a_filename_is_escaped_in_the_href(self):
        entry = IndexEntry(
            channel="CassetteLore",
            title="Fire & Blood - formatted",
            filename="CassetteLore — Fire & Blood - formatted - readview.html",
            date="2026-07-31",
        )
        html = render_index([entry])
        assert "Fire &amp; Blood" in html
        assert 'href="CassetteLore — Fire & Blood' not in html


class TestRowsCarryWhatTheHandlerNeeds:
    def test_each_row_is_keyed_by_its_filename(self):
        html = render_index(REAL_ENTRIES)
        # The swipe handler persists read state under the filename, which carries
        # the draft version - that is what makes a new draft come back unread.
        for entry in REAL_ENTRIES:
            assert f'data-key="{escape(entry.filename, quote=True)}"' in html

    def test_each_section_is_keyed_by_its_raw_channel(self):
        html = render_index(REAL_ENTRIES)
        assert 'data-channel="CassetteLore"' in html
        assert 'data-channel="Birds of Play"' in html

    def test_rows_carry_their_position_within_the_channel(self):
        # Unmarking a script puts it back in title order rather than leaving it
        # at the bottom, which needs the original position on each row.
        html = render_index(
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
        html = render_index(REAL_ENTRIES)
        assert len(re.findall(r'class="check"', html)) == len(REAL_ENTRIES)

    def test_the_date_is_shown(self):
        html = render_index([_entry("CassetteLore", "Thing - formatted", date="2026-01-09")])
        assert "2026-01-09" in html


class TestLength:
    """Length is what decides whether a script fits the time available."""

    def test_word_count_is_shown_with_thousands_separators(self):
        html = render_index([_entry("CassetteLore", "Thing - formatted", words=1340)])
        assert "1,340 words" in html

    def test_length_leads_the_sub_line_ahead_of_the_date(self):
        html = render_index(
            [_entry("CassetteLore", "Thing - formatted", date="2026-01-09", words=1340)]
        )
        # Both facts on one line, length first. Asserting order rather than mere
        # presence: a row that rendered them in two places would still pass a
        # containment check.
        assert "1,340 words &middot; 2026-01-09" in html

    def test_a_missing_count_degrades_to_the_date_alone(self):
        html = render_index(
            [_entry("CassetteLore", "Thing - formatted", date="2026-01-09", words=None)]
        )
        assert "<span>2026-01-09</span>" in html
        assert "words" not in html
        # And the row is still reachable, which is the whole point of degrading.
        assert "<b>Thing - formatted</b>" in html

    def test_a_zero_count_is_shown_rather_than_treated_as_missing(self):
        # 0 is falsy. A read-view that really did extract no words should say so,
        # not silently look like an older file that carries no count at all.
        html = render_index([_entry("CassetteLore", "Empty - formatted", words=0)])
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
        path.write_text(_readview(None).replace("data-title", 'data-words="lots" data-title'))
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
        assert "<b>B v1 - formatted</b>" in html
        assert html.count("words") == 1


class TestSelfContainment:
    """It runs as `python3 -` on the Pi with nothing installed."""

    def test_module_imports_only_the_stdlib(self):
        source = LIBRARY_PY.read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, re.M)
        assert imports, "no imports found - did the file move?"
        for name in imports:
            root = name.split(".")[0]
            assert root != "vo_format", f"relative/package import would break piping: {name}"
            assert root in sys.stdlib_module_names, f"non-stdlib import: {name}"

    def test_page_references_no_external_host(self):
        html = render_index(REAL_ENTRIES)
        assert "http://" not in html
        assert "https://" not in html

    def test_runs_as_a_piped_script(self, tmp_path: Path):
        for entry in REAL_ENTRIES:
            (tmp_path / entry.filename).write_text("<html></html>", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-", str(tmp_path)],
            input=LIBRARY_PY.read_text(encoding="utf-8"),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "Cassette Lore" in html
        assert "Warcraft Episode 1" in html


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

    @pytest.mark.parametrize("variant", ["formatted", "batched"])
    def test_variant_survives_into_the_title(self, tmp_path: Path, variant: str):
        (tmp_path / f"CassetteLore — A v1 - {variant} - readview.html").write_text("x")
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert variant in html

    def test_readview_suffix_is_stripped_from_the_display_title(self, tmp_path: Path):
        (tmp_path / "CassetteLore — A v1 - formatted - readview.html").write_text("x")
        main(str(tmp_path))
        html = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "readview.html</b>" not in html
        assert "<b>A v1 - formatted</b>" in html
