"""Fidelity tests for readview extraction.

The claim under test is that ColdRead's PDFs are born-digital, so everything the
formatter put in is exactly recoverable. Each test pins one recovered property.
"""

from __future__ import annotations

import re

import pytest

from vo_format.models import Archetype, BlockType, FormattedBlock
from vo_format.readview.extract import ReadViewError, extract_lines

NON_TEXT_BLOCKS = frozenset(
    {BlockType.BLANK_LINE, BlockType.PAGE_BREAK, BlockType.SECTION_DIVIDER}
)


def _fold(s: str) -> str:
    """Collapse whitespace and drop emphasis markers.

    pdf_writer converts **bold** to real bold, so the asterisks are absent from
    the PDF text layer while present in the block. Comparing without folding
    yields false negatives.
    """
    return re.sub(r"\s+", " ", s.replace("*", "").replace("_", "")).strip()


class TestRoundTrip:
    @pytest.mark.parametrize("archetype", list(Archetype))
    def test_every_block_text_survives_the_pdf(self, archetype, sample_pdf) -> None:
        pdf_path, blocks = sample_pdf(archetype)

        script = extract_lines(pdf_path)
        haystack = _fold(" ".join(line.text for line in script.lines))

        missing = [
            b.text
            for b in blocks
            if b.block_type not in NON_TEXT_BLOCKS
            and _fold(b.text)
            and _fold(b.text) not in haystack
        ]
        assert not missing, f"{len(missing)} block(s) lost, first: {missing[:3]!r}"

    def test_line_and_word_counts_are_populated(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        script = extract_lines(pdf_path)
        assert len(script.lines) > 10
        assert script.word_count > len(script.lines)
        assert script.page_count >= 1
        assert script.words_per_line > 1.0

    def test_title_is_the_pdf_stem(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        assert extract_lines(pdf_path).title == pdf_path.stem

    def test_derived_is_iso_8601(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        from datetime import datetime

        datetime.fromisoformat(extract_lines(pdf_path).derived)


class TestStyleFidelity:
    def test_colors_round_trip(self, blocks_pdf) -> None:
        blocks = [
            FormattedBlock(BlockType.DIALOGUE, "Zebra line one", color="#2563EB"),
            FormattedBlock(BlockType.DIALOGUE, "Zebra line two", color="#DC2626"),
            FormattedBlock(BlockType.DIALOGUE, "Zebra line three", color="#16A34A"),
        ]
        script = extract_lines(blocks_pdf(blocks))
        found = {
            line.color.lower()
            for line in script.lines
            if line.text.startswith("Zebra")
        }
        assert {"#2563eb", "#dc2626", "#16a34a"} <= found

    def test_bold_and_italic_round_trip(self, blocks_pdf) -> None:
        blocks = [
            FormattedBlock(BlockType.DIALOGUE, "Plainmarker text"),
            FormattedBlock(BlockType.DIALOGUE, "Boldmarker text", bold=True),
            FormattedBlock(BlockType.DIALOGUE, "Italicmarker text", italic=True),
        ]
        script = extract_lines(blocks_pdf(blocks))
        by_marker = {
            line.text.split()[0]: line for line in script.lines if line.text.split()
        }
        assert by_marker["Boldmarker"].bold is True
        assert by_marker["Italicmarker"].italic is True
        assert by_marker["Plainmarker"].bold is False
        assert by_marker["Plainmarker"].italic is False

    def test_indent_is_relative_and_monotonic(self, blocks_pdf) -> None:
        blocks = [
            FormattedBlock(BlockType.PROSE, "Flushleft marker", indent_level=0),
            FormattedBlock(BlockType.DIALOGUE, "Onetab marker", indent_level=1),
            FormattedBlock(BlockType.QUOTED_TEXT, "Twotab marker", indent_level=2),
        ]
        script = extract_lines(blocks_pdf(blocks))
        got = {
            line.text.split()[0]: line.indent
            for line in script.lines
            if line.text.split()
        }
        assert got["Flushleft"] == 0, "leftmost line must be indent 0"
        assert got["Flushleft"] < got["Onetab"] < got["Twotab"]

    def test_size_ratio_is_one_for_body_and_larger_for_headers(
        self, blocks_pdf
    ) -> None:
        blocks = [FormattedBlock(BlockType.PROSE, f"Body line {i}") for i in range(20)]
        blocks.insert(0, FormattedBlock(BlockType.SECTION_HEADER, "Bigheader marker"))
        script = extract_lines(blocks_pdf(blocks))
        body = [line for line in script.lines if line.text.startswith("Body")]
        header = next(
            line for line in script.lines if line.text.startswith("Bigheader")
        )
        assert all(line.size_ratio == pytest.approx(1.0) for line in body)
        assert header.size_ratio > 1.0

    def test_gap_before_marks_paragraph_breaks_only(self, blocks_pdf) -> None:
        blocks = [
            FormattedBlock(BlockType.PROSE, "Alpha marker"),
            FormattedBlock(BlockType.PROSE, "Bravo marker"),
            FormattedBlock(BlockType.BLANK_LINE, ""),
            FormattedBlock(BlockType.PROSE, "Charlie marker"),
        ]
        script = extract_lines(blocks_pdf(blocks))
        got = {
            line.text.split()[0]: line.gap_before
            for line in script.lines
            if line.text.split()
        }
        assert got["Charlie"] is True, "a blank line must produce gap_before"
        assert got["Bravo"] is False, "consecutive lines must not"


class TestFailsLoudly:
    def test_a_pdf_with_no_text_layer_raises(self, tmp_path) -> None:
        import fitz

        doc = fitz.open()
        doc.new_page()
        empty = tmp_path / "blank.pdf"
        doc.save(str(empty))
        doc.close()

        with pytest.raises(ReadViewError, match="no extractable text"):
            extract_lines(empty)

    def test_a_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises(ReadViewError):
            extract_lines(tmp_path / "nope.pdf")
