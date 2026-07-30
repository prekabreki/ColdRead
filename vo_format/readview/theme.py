"""Map ColdRead's print palette onto a dark background.

colors.py says its palette is "optimized for print grayscale" and makes the
narrator pure black — on a dark background the body text would be invisible and
the blue would be barely legible. But those colors are the voice-switch cue for
a performer voicing every character, so they cannot be discarded, only
translated.

Each character color below holds its print hue and saturation and moves only in
lightness, onto an evenly spaced luminance ladder that preserves the print
palette's own ordering. That keeps hue identity (a blue character stays blue),
clears WCAG AA against the background, and keeps all eight separable in
grayscale.
"""

from __future__ import annotations

import colorsys

# Near-black rather than pure black: pure black behind bright monospace produces
# halation that softens glyph edges at booth reading distance.
DARK_BACKGROUND = "#121212"

DARK_MAP: dict[str, str] = {
    # PALETTE, hue-preserved, luminance-laddered
    "#2563eb": "#729af2",  # Blue
    "#dc2626": "#ed9191",  # Red
    "#16a34a": "#82eeaa",  # Green
    "#9333ea": "#b06bf0",  # Purple
    "#ea580c": "#fac2a5",  # Orange
    "#0891b2": "#41d3f6",  # Cyan
    "#ca8a04": "#fddf9f",  # Amber
    "#db2777": "#efa0c3",  # Pink
    # Structural colors
    "#000000": "#e8e6e3",  # narrator / body — off-white, not #FFFFFF
    "#6b7280": "#9aa2ad",  # stage direction — secondary
    "#9ca3af": "#7b828c",  # sound cue — quieter still
}

# Any color not in DARK_MAP is lifted to at least this luminance so it cannot
# vanish into the background.
_MIN_FALLBACK_LUMINANCE = 0.30


def _channels(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected a 6-digit hex color, got {hex_color!r}")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _linearize(channel: int) -> float:
    ratio = channel / 255
    if ratio <= 0.04045:
        return ratio / 12.92
    return ((ratio + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance, 0.0 (black) to 1.0 (white)."""
    red, green, blue = (_linearize(c) for c in _channels(hex_color))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two colors, 1.0 to 21.0."""
    lum_a, lum_b = relative_luminance(a), relative_luminance(b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _lift(hex_color: str, target: float) -> str:
    """Raise a color's lightness to hit `target` luminance, holding hue."""
    red, green, blue = (c / 255 for c in _channels(hex_color))
    hue, _lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    low, high = 0.0, 1.0
    for _ in range(40):
        mid = (low + high) / 2
        candidate = colorsys.hls_to_rgb(hue, mid, saturation)
        as_hex = "#%02x%02x%02x" % tuple(round(c * 255) for c in candidate)
        if relative_luminance(as_hex) < target:
            low = mid
        else:
            high = mid
    final = colorsys.hls_to_rgb(hue, (low + high) / 2, saturation)
    return "#%02x%02x%02x" % tuple(round(c * 255) for c in final)


def dark_color(print_hex: str) -> str:
    """The dark-background counterpart of a print color.

    Known palette colors come from DARK_MAP. Anything else is lifted to a
    legible luminance rather than passed through, so an unrecognized color
    degrades instead of disappearing.
    """
    key = print_hex.lower()
    if key in DARK_MAP:
        return DARK_MAP[key]
    if relative_luminance(key) >= _MIN_FALLBACK_LUMINANCE:
        return key
    return _lift(key, _MIN_FALLBACK_LUMINANCE)
