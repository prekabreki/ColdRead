"""Toggle-resolution priority and archetype-default tests."""

from __future__ import annotations

from vo_format.models import Archetype, FormatToggles, MarginPreset, QuotedTextStyle
from vo_format.toggles import ARCHETYPE_DEFAULTS, resolve_toggles


def test_defaults_when_nothing_is_passed():
    t = resolve_toggles(Archetype.MULTI_VOICE_DRAMA)
    # Archetype defaults should pick up multi_voice_drama overrides.
    assert t.color_characters is True
    assert t.character_legend is True
    assert t.quoted_text_style == QuotedTextStyle.NONE


def test_archetype_defaults_applied_over_global():
    # CONTINUOUS_PROSE turns breathing_marks on; global default is off.
    t = resolve_toggles(Archetype.CONTINUOUS_PROSE)
    assert t.breathing_marks is True
    assert t.color_characters is False


def test_preflight_suggestions_override_archetype():
    # multi_voice_drama default: color_characters=True.
    # Preflight suggests False — preflight wins over archetype.
    t = resolve_toggles(
        Archetype.MULTI_VOICE_DRAMA,
        preflight_suggestions={"color_characters": False},
    )
    assert t.color_characters is False


def test_cli_overrides_beat_preflight():
    t = resolve_toggles(
        Archetype.MULTI_VOICE_DRAMA,
        preflight_suggestions={"color_characters": False},
        cli_overrides={"color_characters": True},
    )
    assert t.color_characters is True


def test_cli_none_does_not_override():
    # None means "not set on CLI" — must fall through to lower-priority layers.
    t = resolve_toggles(
        Archetype.MULTI_VOICE_DRAMA,
        preflight_suggestions={"color_characters": False},
        cli_overrides={"color_characters": None},
    )
    assert t.color_characters is False

def test_enum_coercion_from_string():
    t = resolve_toggles(
        Archetype.MULTI_VOICE_DRAMA,
        cli_overrides={"margins": "extra", "quoted_text_style": "italic"},
    )
    assert t.margins == MarginPreset.EXTRA
    assert t.quoted_text_style == QuotedTextStyle.ITALIC


def test_every_archetype_resolves():
    # No archetype should crash or leave a field unset.
    for arch in Archetype:
        t = resolve_toggles(arch)
        assert isinstance(t, FormatToggles)
        # Every known archetype must appear in the defaults map.
        assert arch in ARCHETYPE_DEFAULTS
