"""Tests for vo_format.gui pure helpers (no Tk root required)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Import guard — skip all tests if customtkinter or a display is unavailable
# ---------------------------------------------------------------------------

pytest.importorskip("customtkinter", reason="customtkinter not available (headless CI)")

_gui_module = pytest.importorskip(
    "vo_format.gui",
    reason="vo_format.gui import failed (missing deps or no display)",
)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestSnapSpacing:
    def test_zero(self) -> None:
        assert _gui_module._snap_spacing(0.0) == 0.0

    def test_exact_quarters(self) -> None:
        for v in (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0):
            assert _gui_module._snap_spacing(v) == v

    def test_round_down(self) -> None:
        assert _gui_module._snap_spacing(0.1) == 0.0
        assert _gui_module._snap_spacing(0.124) == 0.0
        assert _gui_module._snap_spacing(0.24) == 0.25
        assert _gui_module._snap_spacing(0.49) == 0.5

    def test_round_up(self) -> None:
        assert _gui_module._snap_spacing(0.126) == 0.25
        assert _gui_module._snap_spacing(0.26) == 0.25
        assert _gui_module._snap_spacing(0.38) == 0.5
        assert _gui_module._snap_spacing(0.51) == 0.5

    def test_boundary_bankers_rounding(self) -> None:
        assert _gui_module._snap_spacing(0.125) == 0.0
        assert _gui_module._snap_spacing(0.375) == 0.5
        assert _gui_module._snap_spacing(0.625) == 0.5
        assert _gui_module._snap_spacing(0.875) == 1.0

    def test_float_precision_edges(self) -> None:
        assert _gui_module._snap_spacing(0.1249999999) == 0.0
        assert _gui_module._snap_spacing(0.1250000001) == 0.25


class TestFindApiKey:
    def test_env_var_set(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        assert _gui_module._find_api_key() == "sk-ant-test-key"

    def test_env_var_not_set(self, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert _gui_module._find_api_key() is None

    def test_env_var_empty_string(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        assert _gui_module._find_api_key() is None


# ---------------------------------------------------------------------------
# VOFormatterApp static helpers
# ---------------------------------------------------------------------------

from vo_format.models import BlockType, FormattedBlock  # noqa: E402


def _b(block_type: BlockType, text: str = "", **kw) -> FormattedBlock:
    """Shorthand for building a FormattedBlock in tests."""
    return FormattedBlock(block_type=block_type, text=text, **kw)


class TestTextToParagraphBlocks:
    def test_empty(self) -> None:
        assert _gui_module.VOFormatterApp._text_to_paragraph_blocks("", BlockType.INTRO) == []  # type: ignore[arg-type]
        assert _gui_module.VOFormatterApp._text_to_paragraph_blocks("   \n  ", BlockType.INTRO) == []  # type: ignore[arg-type]

    def test_single_paragraph(self) -> None:
        result = _gui_module.VOFormatterApp._text_to_paragraph_blocks(
            "Hello world", BlockType.INTRO
        )
        assert result == [_b(BlockType.INTRO, "Hello world")]

    def test_multiple_paragraphs(self) -> None:
        text = "Para one\n\nPara two\n\nPara three"
        result = _gui_module.VOFormatterApp._text_to_paragraph_blocks(
            text, BlockType.OUTRO
        )
        assert result == [
            _b(BlockType.OUTRO, "Para one"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.OUTRO, "Para two"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.OUTRO, "Para three"),
        ]

    def test_multiline_paragraphs(self) -> None:
        text = "Line 1\nLine 2\nLine 3\n\nLine 4"
        result = _gui_module.VOFormatterApp._text_to_paragraph_blocks(
            text, BlockType.INTRO
        )
        assert result == [
            _b(BlockType.INTRO, "Line 1\nLine 2\nLine 3"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.INTRO, "Line 4"),
        ]

    def test_leading_trailing_blank_lines(self) -> None:
        text = "\n\n\nPara\n\n\n"
        result = _gui_module.VOFormatterApp._text_to_paragraph_blocks(
            text, BlockType.INTRO
        )
        assert result == [_b(BlockType.INTRO, "Para")]

    def test_strips_trailing_spaces(self) -> None:
        text = "Para one   \n  Trailing spaces   \n\nPara two"
        result = _gui_module.VOFormatterApp._text_to_paragraph_blocks(
            text, BlockType.INTRO
        )
        assert result == [
            _b(BlockType.INTRO, "Para one\n  Trailing spaces"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.INTRO, "Para two"),
        ]


class TestWrapWithIntroOutro:
    """_wrap_with_intro_outro inserts intro after title/legend frontmatter
    and appends outro at the very end."""

    def test_no_intro_no_outro(self) -> None:
        blocks = [_b(BlockType.DIALOGUE, "Hello")]
        result = _gui_module.VOFormatterApp._wrap_with_intro_outro(blocks, [], [])
        assert result is blocks  # same object returned

    def test_intro_only_no_frontmatter(self) -> None:
        blocks = [
            _b(BlockType.DIALOGUE, "Line 1"),
            _b(BlockType.DIALOGUE, "Line 2"),
        ]
        intro = [_b(BlockType.INTRO, "Intro text")]
        result = _gui_module.VOFormatterApp._wrap_with_intro_outro(blocks, intro, [])
        assert result == [
            _b(BlockType.INTRO, "Intro text"),
            _b(BlockType.DIALOGUE, "Line 1"),
            _b(BlockType.DIALOGUE, "Line 2"),
        ]

    def test_outro_only(self) -> None:
        blocks = [_b(BlockType.DIALOGUE, "Line 1")]
        outro = [_b(BlockType.OUTRO, "Outro text")]
        result = _gui_module.VOFormatterApp._wrap_with_intro_outro(blocks, [], outro)
        assert result == [
            _b(BlockType.DIALOGUE, "Line 1"),
            _b(BlockType.OUTRO, "Outro text"),
        ]

    def test_intro_after_title_page(self) -> None:
        blocks = [
            _b(BlockType.TITLE_PAGE_TITLE, "My Script"),
            _b(BlockType.TITLE_PAGE_INFO, "Author: Me"),
            _b(BlockType.SECTION_DIVIDER, "---"),
            _b(BlockType.DIALOGUE, "Line 1"),
        ]
        intro = [_b(BlockType.INTRO, "Intro text")]
        result = _gui_module.VOFormatterApp._wrap_with_intro_outro(blocks, intro, [])
        assert result == [
            _b(BlockType.TITLE_PAGE_TITLE, "My Script"),
            _b(BlockType.TITLE_PAGE_INFO, "Author: Me"),
            _b(BlockType.SECTION_DIVIDER, "---"),
            _b(BlockType.INTRO, "Intro text"),
            _b(BlockType.DIALOGUE, "Line 1"),
        ]

    def test_intro_after_character_legend(self) -> None:
        blocks = [
            _b(BlockType.CHARACTER_LEGEND_HEADER, "Characters"),
            _b(BlockType.CHARACTER_LEGEND_ENTRY, "ALICE - Blue"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.DIALOGUE, "Line 1"),
        ]
        intro = [_b(BlockType.INTRO, "Intro text")]
        result = _gui_module.VOFormatterApp._wrap_with_intro_outro(blocks, intro, [])
        assert result == [
            _b(BlockType.CHARACTER_LEGEND_HEADER, "Characters"),
            _b(BlockType.CHARACTER_LEGEND_ENTRY, "ALICE - Blue"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.INTRO, "Intro text"),
            _b(BlockType.DIALOGUE, "Line 1"),
        ]

    def test_intro_after_title_and_legend_with_page_break(self) -> None:
        blocks = [
            _b(BlockType.TITLE_PAGE_TITLE, "My Script"),
            _b(BlockType.TITLE_PAGE_INFO, "Author: Me"),
            _b(BlockType.PAGE_BREAK, ""),
            _b(BlockType.CHARACTER_LEGEND_HEADER, "Characters"),
            _b(BlockType.CHARACTER_LEGEND_ENTRY, "ALICE - Blue"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.PAGE_BREAK, ""),
            _b(BlockType.DIALOGUE, "Line 1"),
        ]
        intro = [_b(BlockType.INTRO, "Intro text")]
        result = _gui_module.VOFormatterApp._wrap_with_intro_outro(blocks, intro, [])
        assert result == [
            _b(BlockType.TITLE_PAGE_TITLE, "My Script"),
            _b(BlockType.TITLE_PAGE_INFO, "Author: Me"),
            _b(BlockType.PAGE_BREAK, ""),
            _b(BlockType.CHARACTER_LEGEND_HEADER, "Characters"),
            _b(BlockType.CHARACTER_LEGEND_ENTRY, "ALICE - Blue"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.PAGE_BREAK, ""),
            _b(BlockType.INTRO, "Intro text"),
            _b(BlockType.DIALOGUE, "Line 1"),
        ]

    def test_both_intro_and_outro(self) -> None:
        blocks = [
            _b(BlockType.TITLE_PAGE_TITLE, "Title"),
            _b(BlockType.DIALOGUE, "Body"),
        ]
        intro = [_b(BlockType.INTRO, "Intro")]
        outro = [_b(BlockType.OUTRO, "Outro")]
        result = _gui_module.VOFormatterApp._wrap_with_intro_outro(blocks, intro, outro)
        assert result == [
            _b(BlockType.TITLE_PAGE_TITLE, "Title"),
            _b(BlockType.INTRO, "Intro"),
            _b(BlockType.DIALOGUE, "Body"),
            _b(BlockType.OUTRO, "Outro"),
        ]

    def test_intro_consumes_trailing_separators_after_frontmatter(self) -> None:
        blocks = [
            _b(BlockType.TITLE_PAGE_TITLE, "Title"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.SECTION_DIVIDER, "---"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.DIALOGUE, "Body"),
        ]
        intro = [_b(BlockType.INTRO, "Intro")]
        result = _gui_module.VOFormatterApp._wrap_with_intro_outro(blocks, intro, [])
        assert result == [
            _b(BlockType.TITLE_PAGE_TITLE, "Title"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.SECTION_DIVIDER, "---"),
            _b(BlockType.BLANK_LINE, ""),
            _b(BlockType.INTRO, "Intro"),
            _b(BlockType.DIALOGUE, "Body"),
        ]


# ---------------------------------------------------------------------------
# _reap_claude — no-op when no process is tracked
# ---------------------------------------------------------------------------


class MockApp:
    """Minimal stand-in for VOFormatterApp so we can test _reap_claude
    without a Tk root or live subprocess."""

    def __init__(self, claude_proc=None):
        self._claude_proc = claude_proc

    def _reap_claude(self):
        _gui_module.VOFormatterApp._reap_claude(self)


class TestReapClaudeNoOp:
    """_reap_claude must be a no-op when no Claude child is tracked."""

    def test_no_tracked_process_does_nothing(self):
        app = MockApp()
        app._reap_claude()  # must not raise

    @patch("subprocess.run")
    @patch("subprocess.Popen")
    def test_no_tracked_process_spawns_nothing(self, mock_popen, mock_run):
        app = MockApp()
        app._reap_claude()
        mock_run.assert_not_called()
        mock_popen.assert_not_called()
