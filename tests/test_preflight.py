"""Tests for preflight JSON parsing and validation (no API key needed)."""

from __future__ import annotations

import pytest

from vo_format.models import Archetype, PreflightResult
from vo_format.preflight import JSONParseError, ValidationError, _extract_json, _validate_and_build


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------


def test_extract_json_raw():
    result = _extract_json('{"archetype": "document_archive"}')
    assert result == {"archetype": "document_archive"}


def test_extract_json_fenced():
    text = 'Some text before\n```json\n{"archetype": "multi_voice_drama"}\n```\nSome after'
    result = _extract_json(text)
    assert result == {"archetype": "multi_voice_drama"}


def test_extract_json_fenced_no_lang():
    text = '```\n{"archetype": "single_narrator"}\n```'
    result = _extract_json(text)
    assert result == {"archetype": "single_narrator"}


def test_extract_json_prose_preamble():
    text = (
        'Here is my analysis of the script.\n'
        'After reviewing carefully, I see this is a drama.\n'
        '{"archetype": "multi_voice_drama", "characters": [{"name": "ALICE", "line_count": 10, "suggested_color": "#FF0000"}]}\n'
        'Please let me know if you need more.'
    )
    result = _extract_json(text)
    assert result["archetype"] == "multi_voice_drama"


def test_extract_json_garbage():
    with pytest.raises(JSONParseError):
        _extract_json("This is not JSON at all. No braces, no nothing.")


def test_extract_json_empty():
    with pytest.raises(JSONParseError):
        _extract_json("")


def test_extract_json_partial_braces_no_close():
    with pytest.raises(JSONParseError):
        _extract_json('{"archetype": "document_archive"')


# ---------------------------------------------------------------------------
# _validate_and_build
# ---------------------------------------------------------------------------


def test_validate_bad_archetype():
    data = {"archetype": "not_a_real_archetype"}
    with pytest.raises(ValidationError, match="Unknown archetype"):
        _validate_and_build(data)


def test_validate_missing_end_line():
    data = {
        "archetype": "document_archive",
        "metadata_blocks": [
            {"type": "editorial_note", "start_line": 5, "text": "Fix this later"}
        ],
    }
    result = _validate_and_build(data)
    assert len(result.metadata_blocks) == 1
    mb = result.metadata_blocks[0]
    assert mb.start_line == 5
    assert mb.end_line == 5  # clamped to start_line
    assert any("missing end_line" in w for w in result.warnings)


def test_validate_inverted_range():
    data = {
        "archetype": "document_archive",
        "metadata_blocks": [
            {"type": "youtube_title", "start_line": 20, "end_line": 10, "text": "My Video"}
        ],
    }
    result = _validate_and_build(data)
    assert len(result.metadata_blocks) == 1
    mb = result.metadata_blocks[0]
    assert mb.start_line == 20
    assert mb.end_line == 20  # clamped to start_line
    assert any("end_line" in w and "before start_line" in w for w in result.warnings)


def test_validate_happy_path():
    data = {
        "archetype": "multi_voice_drama",
        "characters": [
            {"name": "ALICE", "line_count": 12, "suggested_color": "#FF5733"},
            {"name": "BOB", "line_count": 8, "suggested_color": "#33FF57"},
        ],
        "has_narrator": True,
        "source_types": [
            {"type": "broadcast", "label": "Radio", "prefix": "[RADIO]", "count": 3},
        ],
        "sections": [
            {"title": "Act I", "start_line": 1, "end_line": 50},
        ],
        "detected_stage_directions": True,
        "detected_sound_cues": False,
        "metadata_blocks": [
            {"type": "editorial_note", "start_line": 10, "end_line": 12, "text": "Check timing"},
        ],
        "pronunciation_flags": [
            {"word": "cache", "line": 42},
        ],
        "suggested_toggles": {"color_characters": True},
        "warnings": ["Script has unusually long lines."],
    }
    result = _validate_and_build(data)
    assert isinstance(result, PreflightResult)
    assert result.archetype == Archetype.MULTI_VOICE_DRAMA
    assert len(result.characters) == 2
    assert result.characters[0].name == "ALICE"
    assert result.characters[0].line_count == 12
    assert result.has_narrator is True
    assert len(result.source_types) == 1
    assert result.source_types[0].type == "broadcast"
    assert len(result.sections) == 1
    assert result.sections[0].title == "Act I"
    assert result.detected_stage_directions is True
    assert result.detected_sound_cues is False
    assert len(result.metadata_blocks) == 1
    assert result.metadata_blocks[0].type == "editorial_note"
    assert result.metadata_blocks[0].end_line == 12
    assert len(result.pronunciation_flags) == 1
    assert result.pronunciation_flags[0].word == "cache"
    assert result.suggested_toggles == {"color_characters": True}
    assert "Script has unusually long lines." in result.warnings
