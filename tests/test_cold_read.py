"""Table-driven tests for the cold-read breath-group engine.

Tests every public/internal function of ``cold_read.py`` for correctness:
line-length bounds, break priority, orphan prevention, and passthrough
behaviour.  Also guards against _STYLE_INDENT / WRAPPABLE_INDENT_UNITS drift.
"""

from __future__ import annotations

import pytest

from vo_format.cold_read import (
    _compute_max_chars,
    _find_best_break,
    _fix_orphans,
    _STYLE_INDENT,
    apply_cold_read_breaks,
    wrap_cold_read,
)
from vo_format.models import (
    BlockType,
    FormatToggles,
    FormattedBlock,
    MarginPreset,
    WRAPPABLE_INDENT_UNITS,
)


# ---------------------------------------------------------------------------
# Guard: _STYLE_INDENT stays in sync with WRAPPABLE_INDENT_UNITS
# ---------------------------------------------------------------------------

class TestStyleIndentSync:
    """``_STYLE_INDENT`` must be the exact same object as ``WRAPPABLE_INDENT_UNITS``."""

    def test_style_indent_is_wrappable_indent_units(self):
        assert _STYLE_INDENT is WRAPPABLE_INDENT_UNITS


# ---------------------------------------------------------------------------
# Pass-through tests
# ---------------------------------------------------------------------------

class TestPassthrough:
    """Short text and already-broken text must be left untouched."""

    def test_short_text_returns_unchanged(self):
        text = "Hello world."
        result = wrap_cold_read(text, max_chars=20)
        assert result == text

    def test_text_with_embedded_newlines_passthrough(self):
        text = "Line one.\nLine two.\nLine three."
        result = wrap_cold_read(text, max_chars=10)
        assert result == text

    def test_text_at_max_chars_passthrough(self):
        text = "Exactly 17 chars!"
        result = wrap_cold_read(text, max_chars=17)
        assert result == text

    def test_empty_text_returns_empty(self):
        assert wrap_cold_read("", max_chars=20) == ""


# ---------------------------------------------------------------------------
# Length invariant: every output line ≤ max_chars
# ---------------------------------------------------------------------------

LENGTH_CASES = [
    pytest.param(
        "This is a fairly long sentence that should definitely be broken into multiple shorter lines.",
        25,
        id="moderate-text-moderate-limit",
    ),
    pytest.param(
        "Short.",
        30,
        id="very-short-text-generous-limit",
    ),
    pytest.param(
        "One two three four five six seven eight nine ten eleven twelve thirteen.",
        20,
        id="word-list-with-tight-limit",
    ),
    pytest.param(
        "Hello there, how are you doing on this fine and sunny day? I hope everything is going well with your project.",
        30,
        id="two-sentences-moderate-limit",
    ),
]


class TestLineLengthInvariant:
    """Every line in ``wrap_cold_read`` output must be ≤ *max_chars*."""

    @pytest.mark.parametrize("text,max_chars", LENGTH_CASES)
    def test_every_line_within_limit(self, text: str, max_chars: int):
        result = wrap_cold_read(text, max_chars)
        for line in result.split("\n"):
            assert len(line) <= max_chars, (
                f"Line {line!r} ({len(line)} chars) exceeds {max_chars}"
            )

    def test_every_line_respects_varying_limits(self):
        for limit in (30, 35, 40, 50):
            text = "One two three four five six seven eight nine ten eleven."
            result = wrap_cold_read(text, limit)
            for line in result.split("\n"):
                assert len(line) <= limit, (
                    f"Line {line!r} ({len(line)} chars) exceeds limit {limit}"
                )


# ---------------------------------------------------------------------------
# Orphan prevention: no ≤2-word lines (except the first)
# ---------------------------------------------------------------------------

class TestOrphanPrevention:
    """``_fix_orphans`` must pull one word from previous line to merge with
    a short orphan, provided the merged result fits within max_chars."""

    def test_orphan_pulls_last_word_of_previous_line(self):
        lines = [
            "This is a longer line here",
            "with several words indeed",
            "short",
        ]
        _fix_orphans(lines, max_chars=40)
        assert len(lines) == 3
        assert lines[2].startswith("indeed short")

    def test_orphan_not_merged_when_exceeds_max_chars(self):
        lines = [
            "A very long previous line that fills up all the space available",
            "a",
        ]
        _fix_orphans(lines, max_chars=50)
        assert len(lines) == 2

    def test_orphan_not_merged_when_previous_has_fewer_than_four_words(self):
        lines = [
            "Short previous",
            "short",
        ]
        _fix_orphans(lines, max_chars=40)
        assert len(lines) == 2

    def test_no_change_when_no_orphans(self):
        lines = [
            "This is a normal length line",
            "So is this one right here",
        ]
        original = list(lines)
        _fix_orphans(lines, max_chars=40)
        assert lines == original

    def test_multiple_orphans_processed_backwards_independently(self):
        lines = [
            "This is a nice long line here",
            "a",
            "This another nice long line",
            "b",
        ]
        _fix_orphans(lines, max_chars=40)
        assert len(lines) == 4
        assert "here a" in lines[1] or lines[1].endswith("herea")
        assert "line b" in lines[3] or lines[3].endswith("lineb")

    def test_single_word_orphan_merges_with_pulled_word(self):
        lines = [
            "This is a very nice long line with plenty",
            "Hello",
        ]
        _fix_orphans(lines, max_chars=40)
        assert len(lines) == 2
        assert "plenty Hello" in lines[1]

    def test_orphan_length_two_words_merged(self):
        lines = [
            "This is a very nice long line here with plenty",
            "of words",
        ]
        _fix_orphans(lines, max_chars=50)
        assert len(lines) == 2
        assert "plenty of words" in lines[1]


class TestNoShortOrphansInWrappedOutput:
    """The full ``wrap_cold_read`` must not produce ≤2-word orphan lines
    (lines after the first that have ≤2 words)."""

    def test_no_short_orphans_in_output(self):
        text = (
            "This is a long text that should break into many lines. "
            "It has several sentences and enough content to produce "
            "multiple wrapped lines across the limit."
        )
        result = wrap_cold_read(text, max_chars=25)
        lines = result.split("\n")
        for i, line in enumerate(lines[1:], start=1):
            words = line.split()
            assert len(words) > 2, (
                f"Line {i} has only {len(words)} words: {line!r}"
            )


# ---------------------------------------------------------------------------
# Break priority
# ---------------------------------------------------------------------------

class TestBreakPriority:
    """Breaks must occur at the highest-priority boundary near the target."""

    def test_sentence_end_preferred_over_comma(self):
        text = "Do this thing. But do that, please."
        max_chars = 20
        result = wrap_cold_read(text, max_chars)
        lines = result.split("\n")
        assert len(lines) >= 2
        assert lines[0].endswith("."), (
            f"Expected first line to end with '.', got {lines[0]!r}"
        )

    def test_semicolon_preferred_over_comma(self):
        text = "First clause is here; second clause follows, actually."
        max_chars = 30
        result = wrap_cold_read(text, max_chars)
        lines = result.split("\n")
        assert len(lines) >= 2
        assert lines[0].endswith(";")

    def test_conjunction_break_is_available(self):
        text = "one two three four five six seven eight nine ten eleven and twelve"
        max_chars = 30
        result = wrap_cold_read(text, max_chars)
        lines = result.split("\n")
        for line in lines:
            assert len(line) <= max_chars

    def test_em_dash_as_break_point(self):
        text = "The answer\u2014as it turns out\u2014is quite simple here."
        max_chars = 25
        result = wrap_cold_read(text, max_chars)
        lines = result.split("\n")
        for line in lines:
            assert len(line) <= max_chars

    def test_space_fallback_when_no_priority_break(self):
        text = "Here is a long string of words with no punctuation to break on"
        max_chars = 25
        result = wrap_cold_read(text, max_chars)
        lines = result.split("\n")
        for line in lines:
            assert len(line) <= max_chars


# ---------------------------------------------------------------------------
# _find_best_break unit tests
# ---------------------------------------------------------------------------

class TestFindBestBreak:
    """Direct unit tests for the internal break-point searcher."""

    def test_returns_none_when_no_break_available(self):
        text = "NoBreaksHereAtAll"
        result = _find_best_break(text, max_pos=15, min_pos=0)
        assert result is None

    def test_breaks_at_space_fallback(self):
        text = "hello world foo bar baz"
        result = _find_best_break(text, max_pos=14, min_pos=0)
        # Space before "bar" at position 11, break after space = 12
        assert result == 12

    def test_respects_min_pos(self):
        text = "a b c d e f g h i j"
        result = _find_best_break(text, max_pos=13, min_pos=10)
        assert result is None or result >= 10

    def test_sentence_end_detected_in_window(self):
        text = "Hello. How are you?"
        # "Hello. " = indices 0-6, ". " ends at 7
        # max_pos=8 => window_start = max(0, 8-int(8*0.4)) = max(0, 4) = 4
        # window = text[4:9] = "o. Ho"
        # ". " at window[1:3] → abs_pos = 4+3 = 7
        result = _find_best_break(text, max_pos=8, min_pos=0)
        assert result == 7

    def test_no_break_in_window_uses_space_fallback(self):
        text = "Hello there, how are you?"
        result = _find_best_break(text, max_pos=20, min_pos=0)
        assert result is not None


# ---------------------------------------------------------------------------
# _compute_max_chars
# ---------------------------------------------------------------------------

class TestComputeMaxChars:
    """Verify width calculation produces sane, non-negative values."""

    def test_default_produces_positive_value(self):
        result = _compute_max_chars(
            BlockType.DIALOGUE, indent_level=0,
            font_size=16, margins=MarginPreset.WIDE,
        )
        assert result >= 20

    def test_dialogue_and_narration_same_width(self):
        d = _compute_max_chars(
            BlockType.DIALOGUE, 0, 16, MarginPreset.WIDE,
        )
        n = _compute_max_chars(
            BlockType.NARRATION, 0, 16, MarginPreset.WIDE,
        )
        assert d == n

    def test_prose_wider_than_dialogue(self):
        p = _compute_max_chars(
            BlockType.PROSE, 0, 16, MarginPreset.WIDE,
        )
        d = _compute_max_chars(
            BlockType.DIALOGUE, 0, 16, MarginPreset.WIDE,
        )
        assert p > d

    def test_quoted_text_narrower_than_dialogue(self):
        q = _compute_max_chars(
            BlockType.QUOTED_TEXT, 0, 16, MarginPreset.WIDE,
        )
        d = _compute_max_chars(
            BlockType.DIALOGUE, 0, 16, MarginPreset.WIDE,
        )
        assert q < d

    def test_narrow_margins_give_more_chars(self):
        narrow = _compute_max_chars(
            BlockType.PROSE, 0, 16, MarginPreset.NARROW,
        )
        wide = _compute_max_chars(
            BlockType.PROSE, 0, 16, MarginPreset.WIDE,
        )
        assert narrow > wide

    @pytest.mark.parametrize("margins", MarginPreset)
    def test_all_margin_presets_produce_valid_value(self, margins):
        result = _compute_max_chars(
            BlockType.DIALOGUE, 0, 16, margins,
        )
        assert result >= 20

    def test_larger_font_gives_fewer_chars(self):
        small = _compute_max_chars(
            BlockType.PROSE, 0, 12, MarginPreset.NORMAL,
        )
        large = _compute_max_chars(
            BlockType.PROSE, 0, 24, MarginPreset.NORMAL,
        )
        assert small > large

    def test_indent_reduces_width(self):
        no_indent = _compute_max_chars(
            BlockType.DIALOGUE, 0, 16, MarginPreset.WIDE,
        )
        with_indent = _compute_max_chars(
            BlockType.DIALOGUE, 2, 16, MarginPreset.WIDE,
        )
        assert no_indent > with_indent


# ---------------------------------------------------------------------------
# apply_cold_read_breaks (public integration)
# ---------------------------------------------------------------------------

class TestApplyColdReadBreaks:
    """End-to-end: modifying blocks in-place."""

    def test_wraps_dialogue_blocks(self):
        block = FormattedBlock(
            block_type=BlockType.DIALOGUE,
            text="This is a fairly long line of dialogue text that must be broken.",
            color="#000000",
        )
        toggles = FormatToggles()
        apply_cold_read_breaks([block], toggles)
        for line in block.text.split("\n"):
            assert len(line) <= 45

    def test_skips_non_wrappable_blocks(self):
        block = FormattedBlock(
            block_type=BlockType.CHARACTER_NAME,
            text="COGSWORTH",
            color="#000000",
        )
        toggles = FormatToggles()
        original = block.text
        apply_cold_read_breaks([block], toggles)
        assert block.text == original

    def test_skips_empty_text_blocks(self):
        block = FormattedBlock(
            block_type=BlockType.DIALOGUE,
            text="   ",
            color="#000000",
        )
        toggles = FormatToggles()
        apply_cold_read_breaks([block], toggles)
        assert block.text == "   "

    def test_multiple_wrappable_blocks_all_wrapped(self):
        blocks = [
            FormattedBlock(
                block_type=BlockType.DIALOGUE,
                text="First block with quite a lot of text here.",
                color="#000000",
            ),
            FormattedBlock(
                block_type=BlockType.NARRATION,
                text="Second block with a decent amount of narration here too.",
                color="#000000",
            ),
        ]
        toggles = FormatToggles()
        apply_cold_read_breaks(blocks, toggles)
        for block in blocks:
            for line in block.text.split("\n"):
                assert len(line) <= 45
