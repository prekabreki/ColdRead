"""The dark palette must stay legible and stay grayscale-distinguishable.

Speaker color is the voice-switch cue for a performer voicing every character,
so a palette edit that collapses two characters together is a functional bug,
not a cosmetic one.
"""

from __future__ import annotations

import pytest

from vo_format.colors import (
    NARRATOR_COLOR,
    PALETTE,
    SOUND_CUE_COLOR,
    STAGE_DIRECTION_COLOR,
)
from vo_format.readview.theme import (
    DARK_BACKGROUND,
    DARK_MAP,
    contrast_ratio,
    dark_color,
    relative_luminance,
)

WCAG_AA = 4.5
MIN_GRAYSCALE_GAP = 0.05


class TestCoverage:
    def test_every_color_colors_py_can_emit_is_mapped(self) -> None:
        expected = {hex_value.lower() for hex_value, _name in PALETTE} | {
            NARRATOR_COLOR.lower(),
            STAGE_DIRECTION_COLOR.lower(),
            SOUND_CUE_COLOR.lower(),
        }
        assert expected <= set(DARK_MAP), (
            f"unmapped: {sorted(expected - set(DARK_MAP))}"
        )


class TestLegibility:
    @pytest.mark.parametrize("print_hex", sorted(DARK_MAP))
    def test_each_mapped_color_clears_wcag_aa(self, print_hex: str) -> None:
        ratio = contrast_ratio(DARK_MAP[print_hex], DARK_BACKGROUND)
        dark_hex = DARK_MAP[print_hex]
        assert ratio >= WCAG_AA, (
            f"{print_hex} -> {dark_hex} is {ratio:.2f}:1"
        )

    def test_background_is_not_pure_black(self) -> None:
        # Pure black behind bright monospace haloes at booth distance.
        assert DARK_BACKGROUND.lower() != "#000000"
        assert relative_luminance(DARK_BACKGROUND) > 0.0

    def test_body_text_is_not_pure_white(self) -> None:
        assert DARK_MAP[NARRATOR_COLOR.lower()].lower() != "#ffffff"


class TestGrayscaleDistinctness:
    def test_character_colors_stay_separable_in_grayscale(self) -> None:
        lums = sorted(
            relative_luminance(DARK_MAP[hex_value.lower()])
            for hex_value, _name in PALETTE
        )
        gaps = [b - a for a, b in zip(lums, lums[1:])]
        assert min(gaps) >= MIN_GRAYSCALE_GAP, (
            f"closest pair differs by only {min(gaps):.4f}"
        )

    def test_dark_palette_preserves_the_print_luminance_ordering(self) -> None:
        print_order = [
            h for h, _ in sorted(PALETTE, key=lambda p: relative_luminance(p[0]))
        ]
        dark_order = [
            h
            for h, _ in sorted(
                PALETTE, key=lambda p: relative_luminance(DARK_MAP[p[0].lower()])
            )
        ]
        assert print_order == dark_order


class TestFallback:
    def test_an_unmapped_color_is_lifted_not_dropped(self) -> None:
        lifted = dark_color("#003366")  # a dark blue that is not in the palette
        assert lifted.lower() != "#003366"
        assert contrast_ratio(lifted, DARK_BACKGROUND) >= WCAG_AA

    def test_an_already_light_unmapped_color_is_left_alone(self) -> None:
        assert dark_color("#F5F5F5").lower() == "#f5f5f5"

    def test_lookup_is_case_insensitive(self) -> None:
        assert dark_color("#2563EB") == dark_color("#2563eb")
