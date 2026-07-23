"""Character color palette and assignment logic."""

from __future__ import annotations

from .models import CharacterInfo

# 8-color high-contrast palette from spec (optimized for print grayscale)
PALETTE: list[tuple[str, str]] = [
    ("#2563EB", "Blue"),
    ("#DC2626", "Red"),
    ("#16A34A", "Green"),
    ("#9333EA", "Purple"),
    ("#EA580C", "Orange"),
    ("#0891B2", "Cyan"),
    ("#CA8A04", "Amber"),
    ("#DB2777", "Pink"),
]

NARRATOR_COLOR = "#000000"
BACKGROUND_COLOR = "#FFFFFF"
STAGE_DIRECTION_COLOR = "#6B7280"
SOUND_CUE_COLOR = "#9CA3AF"


def assign_colors(
    characters: list[CharacterInfo],
    has_narrator: bool,
) -> dict[str, str]:
    """Assign colors to characters based on line count.

    Characters are sorted by line_count descending. The character with the
    most lines gets the first palette color (Blue), then Red, etc.
    If there are more than 8 non-narrator characters, colors cycle.

    The narrator (if identified) always gets black. A character whose name
    contains common narrator indicators is treated as narrator.

    Returns a mapping of character name -> hex color string.
    """
    narrator_names = {"narrator", "the narrator"}
    color_map: dict[str, str] = {}

    # Separate narrator(s) from regular characters
    narrators: list[CharacterInfo] = []
    speakers: list[CharacterInfo] = []

    for char in characters:
        if char.name.strip().lower() in narrator_names:
            narrators.append(char)
        else:
            speakers.append(char)

    # If has_narrator is True but no character is explicitly named "Narrator",
    # the narrator role is handled implicitly (text without speaker attribution).
    for n in narrators:
        color_map[n.name] = NARRATOR_COLOR

    used_hexes: set[str] = {NARRATOR_COLOR}

    # Sort speakers by line count descending so the most prominent voice
    # gets the most visually distinct color (Blue).
    speakers_sorted = sorted(speakers, key=lambda c: c.line_count, reverse=True)

    palette_hexes = {p[0].upper() for p in PALETTE}
    for i, char in enumerate(speakers_sorted):
        palette_color = PALETTE[i % len(PALETTE)][0]
        # Prefer the preflight's suggested color if it's a valid hex from our palette
        # and not already assigned to another character.
        if (
            char.suggested_color
            and char.suggested_color.upper() in palette_hexes
            and char.suggested_color.upper() not in {h.upper() for h in used_hexes}
        ):
            color = char.suggested_color
        else:
            color = palette_color
        used_hexes.add(color.upper())
        color_map[char.name] = color

    return color_map
