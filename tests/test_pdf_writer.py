"""Tests for pdf_writer: smoke-test PDF generation + BlockType style coverage.

Every test here requires no API key — preflight is stubbed.
"""

from __future__ import annotations

import os
from pathlib import Path

import fitz
import pytest

from vo_format.formatter import format_script
from vo_format.models import (
    Archetype,
    BlockType,
    FormattedBlock,
    FormatToggles,
    PreflightResult,
)
from vo_format.parser import extract_text, normalize_text
from vo_format.pdf_writer import _BLOCK_STYLE_MAP, generate_pdf
from vo_format.toggles import resolve_toggles


SAMPLE_DIR = Path(__file__).resolve().parent.parent / "vo_format" / "samples"

ARCHETYPE_SAMPLE_MAP: dict[Archetype, str] = {
    Archetype.DOCUMENT_ARCHIVE: "document_archive_sample.md",
    Archetype.MULTI_VOICE_DRAMA: "multi_voice_drama_sample.md",
    Archetype.SINGLE_NARRATOR: "single_narrator_sample.md",
    Archetype.CONTINUOUS_PROSE: "continuous_prose_sample.md",
    Archetype.MIXED_MEDIA: "mixed_media_sample.md",
}


def _empty_preflight(
    archetype: Archetype,
    characters: list | None = None,
    has_narrator: bool = True,
) -> PreflightResult:
    return PreflightResult(
        archetype=archetype,
        characters=characters or [],
        has_narrator=has_narrator,
        source_types=[],
        sections=[],
        detected_stage_directions=False,
        detected_sound_cues=False,
        metadata_blocks=[],
        pronunciation_flags=[],
        suggested_toggles={},
        warnings=[],
    )


def _assert_valid_pdf(path: str, label: str) -> None:
    assert os.path.isfile(path), f"PDF not created for {label}"
    assert os.path.getsize(path) > 0, f"PDF is empty for {label}"
    with open(path, "rb") as f:
        assert f.read(4) == b"%PDF", f"Invalid PDF header for {label}"
    doc = fitz.open(path)
    assert doc.page_count > 0, f"PDF has no pages for {label}"
    doc.close()


class TestSmokePdfFromSamples:
    """Format every sample script and render it to PDF — validates end-to-end."""

    @pytest.mark.parametrize("archetype", list(Archetype))
    def test_each_sample_renders_valid_pdf(self, archetype: Archetype, tmp_path: Path) -> None:
        sample_name = ARCHETYPE_SAMPLE_MAP[archetype]
        sample_path = str(SAMPLE_DIR / sample_name)

        raw_text, _ext = extract_text(sample_path)
        text = normalize_text(raw_text)

        preflight = _empty_preflight(archetype=archetype)
        toggles = resolve_toggles(archetype=archetype)

        blocks = format_script(
            raw_text=text,
            preflight=preflight,
            toggles=toggles,
            filename=sample_name,
        )

        output_path = os.path.join(tmp_path, f"test_{archetype.value}.pdf")
        generate_pdf(blocks=blocks, output_path=output_path, toggles=toggles)

        _assert_valid_pdf(output_path, label=archetype.value)


class TestBlockTypeStyleCoverage:
    """Every BlockType member must resolve to a named style or be handled
    as a structural element (page break, blank line, section divider)."""

    SPECIAL_CASED = frozenset({
        BlockType.PAGE_BREAK,
        BlockType.BLANK_LINE,
        BlockType.SECTION_DIVIDER,
    })

    def test_all_block_types_in_style_map_or_special_cased(self) -> None:
        mapped = set(_BLOCK_STYLE_MAP.keys())
        all_types = set(BlockType)
        uncovered = all_types - mapped - self.SPECIAL_CASED
        assert not uncovered, (
            f"BlockType(s) {[m.value for m in sorted(uncovered, key=lambda x: x.value)]} "
            f"are neither in _BLOCK_STYLE_MAP nor special-cased"
        )

    def test_every_block_type_renders_without_error(self, tmp_path: Path) -> None:
        blocks = [
            FormattedBlock(block_type=bt, text=f"Test {bt.value}")
            for bt in BlockType
        ]
        toggles = FormatToggles()
        output_path = os.path.join(tmp_path, "all_block_types.pdf")
        generate_pdf(blocks=blocks, output_path=output_path, toggles=toggles)
        _assert_valid_pdf(output_path, label="all-block-types")
