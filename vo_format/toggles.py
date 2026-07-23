"""Toggle definitions, archetype defaults, and toggle resolution."""

from __future__ import annotations

import typing
from dataclasses import fields
from enum import Enum
from typing import Any

from .models import (
    Archetype,
    FormatToggles,
    MarginPreset,
    NarratorStyle,
    QuotedTextStyle,
)


# Resolve PEP 563 string annotations to actual types for FormatToggles fields.
_FIELD_TYPE_MAP: dict[str, type] = {}
try:
    _FIELD_TYPE_MAP.update(typing.get_type_hints(FormatToggles))
except Exception:
    pass

# ---------------------------------------------------------------------------
# Toggle metadata — drives CLI flag generation and interactive display
# ---------------------------------------------------------------------------

TOGGLE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "color_characters",
        "type": bool,
        "default": True,
        "description": "Assign distinct colors to each character's lines",
        "display_name": "Color-code characters",
    },
    {
        "name": "narrator_style",
        "type": str,
        "default": "normal",
        "choices": ["normal", "italic", "bold"],
        "description": "Narrator text style",
        "display_name": "Narrator style",
    },
    {
        "name": "section_breaks",
        "type": bool,
        "default": True,
        "description": "Insert page breaks between sections",
        "display_name": "Section breaks",
    },
    {
        "name": "stage_directions",
        "type": bool,
        "default": True,
        "description": "Style stage directions (grey italic)",
        "display_name": "Stage directions",
    },
    {
        "name": "sound_cues",
        "type": bool,
        "default": True,
        "description": "Style sound/tape cues distinctly",
        "display_name": "Sound cues",
    },
    {
        "name": "source_labels",
        "type": bool,
        "default": False,
        "description": "Style document/media source labels as blocks",
        "display_name": "Source labels",
    },
    {
        "name": "quoted_text_style",
        "type": str,
        "default": "indent",
        "choices": ["indent", "italic", "indent+italic", "none"],
        "description": "Styling for in-game quoted text",
        "display_name": "Quoted text style",
    },
    {
        "name": "strip_metadata",
        "type": bool,
        "default": True,
        "description": "Remove production notes, YouTube titles, etc.",
        "display_name": "Strip metadata",
    },
    {
        "name": "title_page",
        "type": bool,
        "default": False,
        "description": "Generate a title page with script info",
        "display_name": "Title page",
    },
    {
        "name": "character_legend",
        "type": bool,
        "default": True,
        "description": "Add color-coded character legend",
        "display_name": "Character legend",
    },
    {
        "name": "breathing_marks",
        "type": bool,
        "default": False,
        "description": "Insert [breath] markers at natural pause points",
        "display_name": "Breathing marks",
    },
    {
        "name": "pause_notation",
        "type": bool,
        "default": False,
        "description": "Convert ... and em-dashes to visual pause markers",
        "display_name": "Pause notation",
    },
    {
        "name": "pronunciation_guide",
        "type": bool,
        "default": False,
        "description": "Add AI-generated phonetic hints for flagged words (extra API call)",
        "display_name": "Pronunciation guide",
    },
    {
        "name": "voice_batch",
        "type": bool,
        "default": False,
        "description": "Reorder script by character for batch recording sessions",
        "display_name": "Voice batch mode",
    },
    {
        "name": "cold_read_breaks",
        "type": bool,
        "default": False,
        "description": "Break lines at natural phrase boundaries for easier cold reading",
        "display_name": "Cold read breaks",
    },
    {
        "name": "font_size",
        "type": int,
        "default": 16,
        "choices": [14, 16, 18],
        "description": "Base font size in points",
        "display_name": "Font size",
    },
    {
        "name": "line_spacing",
        "type": float,
        "default": 2.0,
        "description": "Line spacing multiplier",
        "display_name": "Line spacing",
    },
    {
        "name": "margins",
        "type": str,
        "default": "wide",
        "choices": ["normal", "wide", "extra"],
        "description": "Margin preset (normal=1\", wide=1.5\", extra=2\")",
        "display_name": "Margins",
    },
]

# ---------------------------------------------------------------------------
# Per-archetype default overrides
# ---------------------------------------------------------------------------

ARCHETYPE_DEFAULTS: dict[Archetype, dict[str, Any]] = {
    Archetype.DOCUMENT_ARCHIVE: {
        "color_characters": False,
        "source_labels": True,
        "quoted_text_style": "indent",
        "character_legend": False,
    },
    Archetype.MULTI_VOICE_DRAMA: {
        "color_characters": True,
        "section_breaks": True,
        "stage_directions": True,
        "sound_cues": True,
        "source_labels": False,
        "quoted_text_style": "none",
        "character_legend": True,
    },
    Archetype.SINGLE_NARRATOR: {
        "color_characters": False,
        "section_breaks": True,
        "stage_directions": True,
        "sound_cues": True,
        "source_labels": False,
        "quoted_text_style": "indent+italic",
        "character_legend": False,
    },
    Archetype.CONTINUOUS_PROSE: {
        "color_characters": False,
        "section_breaks": True,
        "stage_directions": False,
        "sound_cues": False,
        "source_labels": False,
        "quoted_text_style": "none",
        "character_legend": False,
        "breathing_marks": True,
    },
    Archetype.MIXED_MEDIA: {
        "color_characters": True,
        "source_labels": True,
        "section_breaks": True,
        "stage_directions": True,
        "sound_cues": True,
        "quoted_text_style": "indent",
        "character_legend": True,
    },
}


# ---------------------------------------------------------------------------
# Toggle resolution
# ---------------------------------------------------------------------------

def coerce_value(name: str, value: Any) -> Any:
    """Coerce a raw value to the appropriate type for a toggle field."""
    field_type = _FIELD_TYPE_MAP.get(name)
    if field_type is None:
        return value

    # Enum types: convert string to enum member
    if isinstance(field_type, type) and issubclass(field_type, Enum):
        if isinstance(value, str):
            return field_type(value)
        return value

    # Bool: handle string representations and coerce from int/None
    if field_type is bool:
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)

    # Int: coerce from string or float
    if field_type is int and not isinstance(value, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    # Float: coerce from string or int
    if field_type is float and not isinstance(value, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value

    return value


def resolve_toggles(
    archetype: Archetype,
    preflight_suggestions: dict[str, Any] | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> FormatToggles:
    """Resolve final toggle values.

    Priority (highest to lowest):
      1. CLI explicit overrides
      2. Preflight suggestions from Claude
      3. Archetype-specific defaults
      4. Global defaults (FormatToggles field defaults)

    Returns a fully populated FormatToggles instance.
    """
    if preflight_suggestions is None:
        preflight_suggestions = {}
    if cli_overrides is None:
        cli_overrides = {}

    # Start with global defaults
    toggles = FormatToggles()

    # Apply archetype defaults
    arch_defaults = ARCHETYPE_DEFAULTS.get(archetype, {})
    for key, value in arch_defaults.items():
        if hasattr(toggles, key):
            setattr(toggles, key, coerce_value(key, value))

    # Apply preflight suggestions (map spec toggle names to our field names)
    toggle_name_map = {
        "color_code_characters": "color_characters",
        "add_section_breaks": "section_breaks",
        "mark_stage_directions": "stage_directions",
    }
    for key, value in preflight_suggestions.items():
        mapped_key = toggle_name_map.get(key, key)
        if hasattr(toggles, mapped_key):
            setattr(toggles, mapped_key, coerce_value(mapped_key, value))

    # Apply CLI overrides (highest priority)
    for key, value in cli_overrides.items():
        if hasattr(toggles, key) and value is not None:
            setattr(toggles, key, coerce_value(key, value))

    return toggles


def toggles_to_display(toggles: FormatToggles) -> list[dict[str, Any]]:
    """Build a display-friendly list of toggle states for the interactive CLI."""
    result = []
    for defn in TOGGLE_DEFINITIONS:
        name = defn["name"]
        current_value = getattr(toggles, name)
        # Convert enums to their string value for display
        if hasattr(current_value, "value"):
            display_value = current_value.value
        else:
            display_value = current_value

        entry: dict[str, Any] = {
            "name": name,
            "display_name": defn["display_name"],
            "description": defn["description"],
            "type": defn["type"],
            "value": display_value,
        }
        if "choices" in defn:
            entry["choices"] = defn["choices"]
        result.append(entry)
    return result
