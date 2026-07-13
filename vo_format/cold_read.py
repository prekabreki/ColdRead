"""Cold-read optimized line breaking for VO scripts.

Breaks dialogue/narration text at natural phrase boundaries so each
line is a "breath group" the voice actor can grab in one glance.
"""

from __future__ import annotations

import re

from .models import (
    BlockType,
    FormattedBlock,
    FormatToggles,
    MarginPreset,
    WRAPPABLE_INDENT_UNITS,
)

# Block types eligible for cold-read wrapping
_WRAPPABLE_TYPES = {
    BlockType.DIALOGUE,
    BlockType.NARRATION,
    BlockType.PROSE,
    BlockType.QUOTED_TEXT,
}

# Canonical mapping lives in models.WRAPPABLE_INDENT_UNITS so pdf_writer.py
# and cold_read.py can't drift out of sync.
_STYLE_INDENT = WRAPPABLE_INDENT_UNITS

# Break-point patterns ordered by priority (strongest first).
# Each regex is searched within the text; matches indicate where a
# line break is permitted.
_BREAK_RULES: list[tuple[re.Pattern, int]] = [
    # Priority 3: sentence endings (.!? optionally followed by closing quote)
    (re.compile(r'[.!?][\u201D"\']*\s'), 3),
    # Priority 2: semicolon / colon clause boundary
    (re.compile(r'[;:]\s'), 2),
    # Priority 2: em-dash (with optional space after)
    (re.compile(r'\u2014\s?'), 2),
    # Priority 1: comma
    (re.compile(r',\s'), 1),
    # Priority 1: break BEFORE a conjunction word
    (re.compile(
        r'\s(?=(?:and|but|or|nor|yet|so|because|since|while|when|if|that|'
        r'which|where|although|though|after|before|until|unless|however|then)\s)',
    ), 1),
]

# Safety factor: compute fewer chars than the theoretical max to
# guarantee ReportLab never re-wraps our already-broken lines.
_SAFETY_FACTOR = 0.82


# ------------------------------------------------------------------
# Width computation
# ------------------------------------------------------------------

def _compute_max_chars(
    block_type: BlockType,
    indent_level: int,
    font_size: int,
    margins: MarginPreset,
) -> int:
    """Compute maximum characters per line for the given layout context.

    Uses the monospace property of Courier New: at any point size the
    advance width of every glyph is ``font_size * 0.6`` PostScript
    points (1/72 inch).

    Accounts for both the ParagraphStyle's built-in ``leftIndent`` and
    any additional ``block.indent_level`` override.
    """
    page_width = 8.5  # US Letter inches

    margin_total = {
        MarginPreset.NORMAL: 2.0,   # 1.0 + 1.0
        MarginPreset.WIDE:   3.0,   # 1.5 + 1.5
        MarginPreset.EXTRA:  4.0,   # 2.0 + 2.0
        MarginPreset.NARROW: 1.6,   # 0.8 + 0.8
    }.get(margins, 3.0)

    # Style default indent + any block-level extra indent
    style_indent = _STYLE_INDENT.get(block_type, 0)
    total_indent_inches = (style_indent + indent_level) * 0.5

    available = page_width - margin_total - total_indent_inches
    char_width = font_size * 0.6 / 72.0

    raw = int(available / char_width)
    return max(20, int(raw * _SAFETY_FACTOR))


# ------------------------------------------------------------------
# Break-point search
# ------------------------------------------------------------------

def _find_best_break(text: str, max_pos: int, min_pos: int = 0) -> int | None:
    """Find the best break position in *text* at or before *max_pos*.

    Scans a window near *max_pos* for the highest-priority break
    pattern.  Returns the character index at which to split (everything
    before the index goes on the current line, the rest continues on
    the next line).  Returns ``None`` when no suitable break exists.

    Parameters
    ----------
    min_pos : int
        Minimum acceptable break position.  Breaks before this index
        are rejected to prevent very short lines.
    """
    # Search window — don't look further back than 40% of max_pos
    # to avoid creating very short lines.
    window_start = max(min_pos, max_pos - int(max_pos * 0.4))
    window = text[window_start:max_pos + 1]

    best_pos: int | None = None
    best_priority = -1

    for pattern, priority in _BREAK_RULES:
        for m in pattern.finditer(window):
            abs_pos = window_start + m.end()
            if min_pos < abs_pos <= max_pos:
                # Prefer higher priority; among equals prefer later position
                if (priority > best_priority
                        or (priority == best_priority
                            and (best_pos is None or abs_pos > best_pos))):
                    best_pos = abs_pos
                    best_priority = priority

    # Fallback: last space before max_pos (but still after min_pos)
    if best_pos is None:
        space_idx = text.rfind(" ", min_pos, max_pos + 1)
        if space_idx > min_pos:
            best_pos = space_idx + 1  # break after the space

    return best_pos


# ------------------------------------------------------------------
# Core wrapping
# ------------------------------------------------------------------

def wrap_cold_read(text: str, max_chars: int) -> str:
    """Wrap *text* at natural phrase boundaries for cold-read optimization.

    Uses a **balanced** split strategy: instead of filling each line to
    the maximum and leaving a short stub at the end, the text is divided
    into roughly equal-length lines.  This prevents straggler words and
    produces a more even, scannable block of text.

    Parameters
    ----------
    text : str
        Block text, may contain inline ``*emphasis*`` / ``**bold**``.
    max_chars : int
        Maximum characters per output line.

    Returns
    -------
    str
        Text with ``\\n`` inserted at computed break points.
    """
    import math

    # Already fits on one line
    if len(text) <= max_chars:
        return text

    # Already contains manual line breaks — leave alone
    if "\n" in text:
        return text

    # --- Balanced split ---
    # Figure out how many lines we need, then aim for equal lengths.
    num_lines = math.ceil(len(text) / max_chars)
    target = len(text) // num_lines

    # Allow the break finder some flexibility around the target
    min_target = int(target * 0.65)
    max_target = min(int(target * 1.35), max_chars)

    lines: list[str] = []
    remaining = text
    lines_remaining = num_lines

    while lines_remaining > 1 and len(remaining) > max_chars:
        # Re-compute the ideal target for the remaining text
        ideal = len(remaining) // lines_remaining
        search_max = min(int(ideal * 1.35), max_chars)
        search_min = max(int(ideal * 0.65), 1)

        break_pos = _find_best_break(remaining, search_max, min_pos=search_min)

        if break_pos is None:
            # Widen search up to the hard max
            break_pos = _find_best_break(remaining, max_chars, min_pos=search_min)

        if break_pos is None:
            # Last resort: any space before max_chars
            space_idx = remaining.rfind(" ", 0, max_chars + 1)
            break_pos = space_idx + 1 if space_idx > 0 else max_chars

        line = remaining[:break_pos].rstrip()
        remaining = remaining[break_pos:].lstrip()
        lines.append(line)
        lines_remaining -= 1

    # Final segment
    if remaining.strip():
        lines.append(remaining.strip())

    # Orphan prevention: merge short lines with their neighbours
    _fix_orphans(lines)

    return "\n".join(lines)


def _fix_orphans(lines: list[str]) -> None:
    """Merge very short lines (<=2 words) with the previous line.

    Modifies *lines* in-place.  Works backwards so fixes don't cascade
    upward unpredictably.
    """
    for i in range(len(lines) - 1, 0, -1):
        words = lines[i].split()
        if len(words) <= 2:
            prev_words = lines[i - 1].split()
            if len(prev_words) >= 4:
                pulled = prev_words.pop()
                lines[i - 1] = " ".join(prev_words)
                lines[i] = pulled + " " + lines[i]


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def apply_cold_read_breaks(
    blocks: list[FormattedBlock],
    toggles: FormatToggles,
) -> None:
    """Apply cold-read line breaks to all wrappable text blocks.

    Modifies ``block.text`` **in-place** by inserting newline characters
    at computed phrase boundaries.
    """
    for block in blocks:
        if block.block_type not in _WRAPPABLE_TYPES:
            continue
        if not block.text or not block.text.strip():
            continue

        max_chars = _compute_max_chars(
            block.block_type,
            block.indent_level,
            toggles.font_size,
            toggles.margins,
        )

        block.text = wrap_cold_read(block.text, max_chars)
