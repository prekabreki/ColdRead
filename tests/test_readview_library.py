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

from vo_format.readview.library import IndexEntry, main, render_index

LIBRARY_PY = Path(__file__).resolve().parents[1] / "vo_format" / "readview" / "library.py"


def _entry(channel: str, title: str, date: str = "2026-07-31") -> IndexEntry:
    return IndexEntry(
        channel=channel,
        title=title,
        filename=f"{channel} — {title} - readview.html",
        date=date,
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

    def test_every_row_has_a_toggle_target(self):
        html = render_index(REAL_ENTRIES)
        assert len(re.findall(r'class="check"', html)) == len(REAL_ENTRIES)

    def test_the_date_is_shown(self):
        html = render_index([_entry("CassetteLore", "Thing - formatted", date="2026-01-09")])
        assert "2026-01-09" in html


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
        assert "loose-script" in html

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
