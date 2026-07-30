"""Shared fixtures for the readview tests.

The readview package consumes finished PDFs, so its tests need real ones.
They are generated here rather than committed: a committed binary fixture can
be silently excluded by a .gitignore rule while local runs stay green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vo_format.formatter import format_script
from vo_format.models import (
    Archetype,
    FormattedBlock,
    FormatToggles,
    PreflightResult,
)
from vo_format.parser import extract_text, normalize_text
from vo_format.pdf_writer import generate_pdf
from vo_format.toggles import resolve_toggles

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "vo_format" / "samples"

ARCHETYPE_SAMPLE_MAP: dict[Archetype, str] = {
    Archetype.DOCUMENT_ARCHIVE: "document_archive_sample.md",
    Archetype.MULTI_VOICE_DRAMA: "multi_voice_drama_sample.md",
    Archetype.SINGLE_NARRATOR: "single_narrator_sample.md",
    Archetype.CONTINUOUS_PROSE: "continuous_prose_sample.md",
    Archetype.MIXED_MEDIA: "mixed_media_sample.md",
}


def _empty_preflight(archetype: Archetype) -> PreflightResult:
    return PreflightResult(
        archetype=archetype,
        characters=[],
        has_narrator=True,
        source_types=[],
        sections=[],
        detected_stage_directions=False,
        detected_sound_cues=False,
        metadata_blocks=[],
        pronunciation_flags=[],
        suggested_toggles={},
        warnings=[],
    )


@pytest.fixture
def sample_pdf(tmp_path: Path):
    """Render a bundled sample to a real PDF.

    Returns a callable: (archetype) -> (pdf_path, blocks) so a test can compare
    what went in against what comes back out.
    """

    def _make(archetype: Archetype) -> tuple[Path, list[FormattedBlock]]:
        sample_name = ARCHETYPE_SAMPLE_MAP[archetype]
        text = normalize_text(extract_text(str(SAMPLE_DIR / sample_name))[0])
        toggles = resolve_toggles(archetype=archetype)
        blocks = format_script(
            raw_text=text,
            preflight=_empty_preflight(archetype),
            toggles=toggles,
            filename=sample_name,
        )
        out = tmp_path / f"{archetype.value}.pdf"
        generate_pdf(blocks=blocks, output_path=str(out), toggles=toggles)
        return out, blocks

    return _make


@pytest.fixture
def blocks_pdf(tmp_path: Path):
    """Render an explicit list of FormattedBlocks to a real PDF.

    Used where a test needs to control colors/indents/gaps exactly rather than
    depend on whatever a sample happens to contain.
    """

    def _make(blocks: list[FormattedBlock], name: str = "blocks.pdf") -> Path:
        out = tmp_path / name
        generate_pdf(blocks=blocks, output_path=str(out), toggles=FormatToggles())
        return out

    return _make
