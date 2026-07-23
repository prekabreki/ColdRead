"""Save / load / list named toggle presets as JSON files."""

from __future__ import annotations

import json
import re
from dataclasses import fields
from pathlib import Path
from typing import Any

from .models import FormatToggles
from .toggles import coerce_value

# ---------------------------------------------------------------------------
# Default presets directory
# ---------------------------------------------------------------------------


def _default_presets_dir() -> Path:
    """Return the default directory for user presets."""
    import os

    env = os.environ.get("VO_FORMATTER_PRESETS_DIR")
    if env:
        return Path(env)
    return Path.home() / ".vo-formatter" / "presets"


# ---------------------------------------------------------------------------
# Built-in presets (always available, cannot be deleted)
# ---------------------------------------------------------------------------
# Each preset is a dict of *overrides* against the FormatToggles defaults.
# Adding a new toggle to FormatToggles automatically picks up the sensible
# default here — no per-preset bookkeeping required.

BUILTIN_PRESETS: dict[str, dict[str, Any]] = {
    "Default": {},
    "Minimal": {
        "color_characters": False,
        "section_breaks": False,
        "stage_directions": False,
        "sound_cues": False,
        "quoted_text_style": "none",
        "character_legend": False,
        "font_size": 14,
        "line_spacing": 1.5,
        "margins": "normal",
    },
    "Full Features": {
        "narrator_style": "italic",
        "source_labels": True,
        "quoted_text_style": "indent+italic",
        "title_page": True,
        "breathing_marks": True,
        "pause_notation": True,
        "pronunciation_guide": True,
        "cold_read_breaks": True,
    },
    "Batch Recording": {
        "title_page": True,
        "voice_batch": True,
        "cold_read_breaks": True,
        "font_size": 18,
        "margins": "extra",
    },
}

# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def toggles_to_dict(toggles: FormatToggles) -> dict[str, Any]:
    """Serialize a FormatToggles instance to a JSON-safe dict."""
    result: dict[str, Any] = {}
    for f in fields(FormatToggles):
        value = getattr(toggles, f.name)
        # Convert enums to their string value
        if hasattr(value, "value"):
            value = value.value
        result[f.name] = value
    return result


def dict_to_toggles(data: dict[str, Any]) -> FormatToggles:
    """Deserialize a dict back to FormatToggles, coercing enum values."""
    toggles = FormatToggles()
    for key, value in data.items():
        if hasattr(toggles, key) and value is not None:
            setattr(toggles, key, coerce_value(key, value))
    return toggles


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def _sanitize_name(name: str) -> str:
    """Turn a human-readable preset name into a safe filename stem."""
    return re.sub(r"[^\w\-]+", "_", name.strip()).strip("_").lower()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save_preset(
    name: str,
    toggles: FormatToggles,
    directory: Path | None = None,
) -> Path:
    """Save *toggles* as a named preset JSON file. Returns the file path."""
    d = directory or _default_presets_dir()
    d.mkdir(parents=True, exist_ok=True)

    payload = {
        "name": name,
        "version": 1,
        "toggles": toggles_to_dict(toggles),
    }
    path = d / f"{_sanitize_name(name)}.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_preset(
    name: str,
    directory: Path | None = None,
) -> FormatToggles:
    """Load a preset by name. Checks builtins first, then disk files."""
    # Builtin?
    if name in BUILTIN_PRESETS:
        return dict_to_toggles(BUILTIN_PRESETS[name])

    # Disk
    d = directory or _default_presets_dir()
    path = d / f"{_sanitize_name(name)}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Preset '{name}' not found")

    data = json.loads(path.read_text(encoding="utf-8"))
    return dict_to_toggles(data.get("toggles", {}))


def list_presets(directory: Path | None = None) -> list[str]:
    """Return sorted list of available preset names (builtins + saved)."""
    names = set(BUILTIN_PRESETS.keys())

    d = directory or _default_presets_dir()
    if d.is_dir():
        for p in d.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                n = data.get("name")
                if n:
                    names.add(n)
            except (json.JSONDecodeError, OSError):
                continue

    return sorted(names)


def delete_preset(
    name: str,
    directory: Path | None = None,
) -> bool:
    """Delete a saved preset. Cannot delete builtins. Returns True if deleted."""
    if name in BUILTIN_PRESETS:
        raise ValueError(f"Cannot delete built-in preset '{name}'")

    d = directory or _default_presets_dir()
    path = d / f"{_sanitize_name(name)}.json"
    if path.is_file():
        path.unlink()
        return True
    return False
