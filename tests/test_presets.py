"""Preset override-merge and serialization tests."""

from __future__ import annotations

from dataclasses import fields

import pytest

from vo_format.models import FormatToggles, MarginPreset
from vo_format.presets import (
    BUILTIN_PRESETS,
    dict_to_toggles,
    load_preset,
    toggles_to_dict,
)


def test_default_preset_matches_format_toggles_defaults():
    t = load_preset("Default")
    assert t == FormatToggles()


def test_minimal_preset_turns_features_off():
    t = load_preset("Minimal")
    assert t.color_characters is False
    assert t.section_breaks is False
    assert t.margins == MarginPreset.NORMAL


def test_full_features_turns_features_on():
    t = load_preset("Full Features")
    assert t.title_page is True
    assert t.pronunciation_guide is True
    assert t.cold_read_breaks is True


def test_unknown_preset_raises():
    with pytest.raises(FileNotFoundError):
        load_preset("nope-not-a-preset-name")


def test_all_builtin_override_keys_are_valid_field_names():
    # Guards against typos: every key in a preset must be a real field.
    valid = {f.name for f in fields(FormatToggles)}
    for name, overrides in BUILTIN_PRESETS.items():
        bad = set(overrides) - valid
        assert not bad, f"Preset {name!r} has unknown fields: {bad}"


def test_toggles_round_trip_through_dict():
    original = FormatToggles()
    original.font_size = 18
    original.margins = MarginPreset.EXTRA
    restored = dict_to_toggles(toggles_to_dict(original))
    assert restored == original
