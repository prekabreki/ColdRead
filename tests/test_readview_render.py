"""The rendered page must be self-contained and structurally faithful.

"Self-contained" is not a nicety: the file is read in a booth off a tablet, and
any external reference is a blank line waiting to happen.
"""

from __future__ import annotations

import re

import pytest

from vo_format.models import Archetype
from vo_format.readview.extract import ReadLine, ReadScript, extract_lines
from vo_format.readview.render import render
from vo_format.readview.theme import DARK_MAP


def _script(lines: list[ReadLine], title: str = "Test Script") -> ReadScript:
    return ReadScript(
        title=title,
        lines=lines,
        word_count=sum(len(line.text.split()) for line in lines),
        page_count=1,
        derived="2026-07-30T12:00:00+00:00",
    )


def _line(text: str, **kwargs) -> ReadLine:
    defaults = dict(
        color="#000000",
        bold=False,
        italic=False,
        size_ratio=1.0,
        indent=0,
        gap_before=False,
    )
    defaults.update(kwargs)
    return ReadLine(text=text, **defaults)  # type: ignore[arg-type]


class TestSelfContainment:
    @pytest.fixture
    def html(self, sample_pdf) -> str:
        pdf_path, _ = sample_pdf(Archetype.MULTI_VOICE_DRAMA)
        return render(extract_lines(pdf_path))

    def test_no_external_script_or_stylesheet(self, html: str) -> None:
        assert "<script src" not in html
        assert "<link" not in html
        assert "@import" not in html

    def test_no_remote_urls(self, html: str) -> None:
        remote = re.findall(r"https?://[^\s\"'<>]+", html)
        assert not remote, f"external URL(s) in output: {remote[:3]}"

    def test_the_only_url_function_is_the_inline_video(self, html: str) -> None:
        for url in re.findall(r"url\(([^)]*)\)", html):
            assert url.strip("\"'").startswith("data:"), url

    def test_css_and_js_are_actually_inlined(self, html: str) -> None:
        assert "<style>" in html and "</style>" in html
        assert "<script>" in html and "</script>" in html
        assert "requestAnimationFrame" in html      # from reader.js
        assert "--line-height" in html              # from reader.css

    def test_keep_awake_video_is_embedded(self, html: str) -> None:
        assert 'id="awake"' in html
        assert "data:video/mp4;base64," in html


class TestStructure:
    def test_every_line_becomes_one_paragraph(self) -> None:
        html = render(_script([_line("alpha"), _line("bravo"), _line("charlie")]))
        assert len(re.findall(r'<p class="l[^"]*"', html)) == 3

    def test_gap_before_adds_the_gap_class(self) -> None:
        html = render(_script([_line("alpha"), _line("bravo", gap_before=True)]))
        assert re.search(r'<p class="l gap"[^>]*>bravo</p>', html)

    def test_bold_and_italic_become_classes(self) -> None:
        html = render(_script([_line("b", bold=True), _line("i", italic=True)]))
        assert 'class="l b"' in html
        assert 'class="l i"' in html

    def test_color_is_emitted_as_its_dark_counterpart(self) -> None:
        html = render(_script([_line("spoken", color="#2563EB")]))
        assert DARK_MAP["#2563eb"] in html
        assert "#2563eb" not in html.lower().split("<style>")[1].split("</style>")[0]

    def test_indent_is_emitted_in_character_units(self) -> None:
        html = render(_script([_line("deep", indent=4)]))
        assert "4ch" in html

    def test_size_ratio_is_emitted_for_non_body_lines(self) -> None:
        html = render(_script([_line("head", size_ratio=1.5), _line("body")]))
        assert "1.5em" in html

    def test_words_per_line_reaches_the_client(self) -> None:
        script = _script([_line("one two three"), _line("four five six")])
        assert 'data-words-per-line="3.0"' in render(script)

    def test_title_and_derived_date_are_shown(self) -> None:
        html = render(_script([_line("x")], title="Kingdom Hearts Dark Road"))
        assert "Kingdom Hearts Dark Road" in html
        assert "2026-07-30" in html


class TestEscaping:
    def test_html_metacharacters_in_script_text_are_escaped(self) -> None:
        html = render(_script([_line("<script>alert(1)</script> & \"quoted\"")]))
        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;alert(1)" in html
        assert "&amp;" in html

    def test_metacharacters_in_the_title_are_escaped(self) -> None:
        html = render(_script([_line("x")], title='Ep "1" <draft> & more'))
        assert "<draft>" not in html
        assert "&lt;draft&gt;" in html


class TestReaderContract:
    """The rendered page must satisfy every element reader.js requires.

    reader.js resolves these ids unconditionally and immediately calls methods on
    the results, so ONE missing id throws and kills the whole script — leaving a
    static page with no controls and no scrolling, and no visible error.
    Deriving the list from reader.js itself keeps the two sides from drifting.
    """

    def test_every_id_reader_js_requires_is_rendered(self) -> None:
        from importlib.resources import files

        js = (files("vo_format.readview") / "reader.js").read_text(encoding="utf-8")
        required = set(re.findall(r'getElementById\("([^"]+)"\)', js))
        assert required, "parsed no ids out of reader.js — the regex needs updating"

        html = render(_script([_line("alpha")]))
        missing = sorted(i for i in required if f'id="{i}"' not in html)
        assert not missing, f"reader.js needs ids the page lacks: {missing}"

    def test_a_line_element_exists_for_line_height_measurement(self) -> None:
        # reader.js does querySelector(".l") to measure real line height.
        assert 'class="l' in render(_script([_line("alpha")]))

    def test_body_carries_both_data_attributes_reader_js_reads(self) -> None:
        html = render(_script([_line("one two three")], title="T"))
        assert "data-words-per-line=" in html
        assert "data-title=" in html
