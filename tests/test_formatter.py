"""Formatter end-to-end tests using stub preflight results.

These exercise format_script for every archetype without touching the Claude
API. They're smoke tests: they check that the formatter produces reasonable
block types for representative input, so regressions in dispatch or line
classification surface immediately.
"""

from __future__ import annotations

from vo_format.formatter import format_script
from vo_format.models import (
    Archetype,
    BlockType,
    CharacterInfo,
    PreflightResult,
)
from vo_format.parser import normalize_text
from vo_format.toggles import resolve_toggles


def _empty_preflight(archetype: Archetype, characters=None, has_narrator=True):
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


def _block_types(blocks):
    return {b.block_type for b in blocks}


def test_multi_voice_drama_recognizes_bold_character_names():
    text = normalize_text(
        "**COGSWORTH:** Testing, one two three.\n\n"
        "**THAELRIN:** I hear you, small one.\n"
    )
    preflight = _empty_preflight(
        Archetype.MULTI_VOICE_DRAMA,
        characters=[
            CharacterInfo(name="COGSWORTH", line_count=1, suggested_color="#2563EB"),
            CharacterInfo(name="THAELRIN", line_count=1, suggested_color="#DC2626"),
        ],
        has_narrator=False,
    )
    toggles = resolve_toggles(Archetype.MULTI_VOICE_DRAMA)
    blocks = format_script(text, preflight, toggles, "test.md")

    types = _block_types(blocks)
    assert BlockType.CHARACTER_NAME in types
    assert BlockType.DIALOGUE in types

    names = [b.text for b in blocks if b.block_type == BlockType.CHARACTER_NAME]
    assert any("COGSWORTH" in n for n in names)
    assert any("THAELRIN" in n for n in names)


def test_continuous_prose_produces_no_character_blocks():
    text = normalize_text(
        "As he sleeps, the fourteen-year-old Sora is haunted by a dream.\n\n"
        "It speaks in riddles and warnings.\n"
    )
    preflight = _empty_preflight(Archetype.CONTINUOUS_PROSE, has_narrator=False)
    toggles = resolve_toggles(Archetype.CONTINUOUS_PROSE)
    blocks = format_script(text, preflight, toggles, "prose.md")

    types = _block_types(blocks)
    assert BlockType.CHARACTER_NAME not in types
    # Should produce prose/narration blocks with the actual text.
    assert any(
        b.block_type in (BlockType.PROSE, BlockType.NARRATION)
        for b in blocks
    )


def test_metadata_blocks_are_stripped_when_toggle_on():
    from vo_format.models import MetadataBlock

    text = normalize_text(
        "Line one is real.\n"
        "YOUTUBE TITLE: Strip me.\n"
        "Line three is real.\n"
    )
    preflight = _empty_preflight(Archetype.SINGLE_NARRATOR)
    preflight.metadata_blocks = [
        MetadataBlock(type="youtube_title", start_line=2, end_line=2)
    ]
    toggles = resolve_toggles(Archetype.SINGLE_NARRATOR)
    assert toggles.strip_metadata is True

    blocks = format_script(text, preflight, toggles, "t.md")
    joined = "\n".join(b.text for b in blocks)
    assert "Strip me" not in joined
    assert "Line one" in joined
    assert "Line three" in joined


def test_every_archetype_runs_without_error():
    # Regression guard: dispatch table covers every archetype.
    text = normalize_text("Just a simple narrative line.\n")
    for arch in Archetype:
        preflight = _empty_preflight(arch)
        toggles = resolve_toggles(arch)
        blocks = format_script(text, preflight, toggles, "x.md")
        assert isinstance(blocks, list)


def test_multi_voice_drama_exact_block_sequence():
    text = normalize_text(
        "**CAPTAIN:** Report.\n\n"
        "*(urgent tone)*\n\n"
        "CREW: All quiet.\n"
    )
    preflight = _empty_preflight(
        Archetype.MULTI_VOICE_DRAMA,
        characters=[
            CharacterInfo(name="CAPTAIN", line_count=2, suggested_color="#2563EB"),
            CharacterInfo(name="CREW", line_count=1, suggested_color="#DC2626"),
        ],
        has_narrator=False,
    )
    toggles = resolve_toggles(Archetype.MULTI_VOICE_DRAMA)
    toggles.character_legend = False
    blocks = format_script(text, preflight, toggles, "test.md")
    assert [b.block_type for b in blocks] == [
        BlockType.CHARACTER_NAME,
        BlockType.DIALOGUE,
        BlockType.BLANK_LINE,
        BlockType.STAGE_DIRECTION,
        BlockType.BLANK_LINE,
        BlockType.CHARACTER_NAME,
        BlockType.DIALOGUE,
        BlockType.BLANK_LINE,
    ]


def test_name_like_line_stays_narration():
    text = normalize_text(
        "The captain gave the order.\n"
        "NOTE: All hands on deck.\n"
        "The crew scrambled.\n"
    )
    preflight = _empty_preflight(
        Archetype.MULTI_VOICE_DRAMA,
        characters=[
            CharacterInfo(name="CAPTAIN", line_count=1, suggested_color="#2563EB"),
        ],
        has_narrator=True,
    )
    toggles = resolve_toggles(Archetype.MULTI_VOICE_DRAMA)
    toggles.title_page = False
    toggles.character_legend = False
    blocks = format_script(text, preflight, toggles, "test.md")
    types = [b.block_type for b in blocks]
    content_types = [t for t in types if t != BlockType.BLANK_LINE]
    assert BlockType.CHARACTER_NAME not in content_types
    assert all(t == BlockType.NARRATION for t in content_types)


def test_metadata_survives_when_strip_metadata_off():
    from vo_format.models import MetadataBlock

    text = normalize_text(
        "Line one.\n"
        "YOUTUBE TITLE: Keep me.\n"
        "Line two.\n"
    )
    preflight = _empty_preflight(Archetype.SINGLE_NARRATOR)
    preflight.metadata_blocks = [
        MetadataBlock(type="youtube_title", start_line=2, end_line=2)
    ]
    toggles = resolve_toggles(Archetype.SINGLE_NARRATOR)
    toggles.strip_metadata = False
    toggles.title_page = False
    toggles.character_legend = False
    blocks = format_script(text, preflight, toggles, "t.md")
    joined = "\n".join(b.text for b in blocks)
    assert "Keep me" in joined
    assert "Line one" in joined
    assert "Line two" in joined
