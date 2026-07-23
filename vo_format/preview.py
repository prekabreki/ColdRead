"""Terminal preview using Rich for styled script rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

from .models import BlockType, FormattedBlock, FormatToggles, NarratorStyle

if TYPE_CHECKING:
    from rich.console import Console


def _print_rule(console: Console, title: str = "", style: str = "dim") -> None:
    """Print an ASCII horizontal rule that won't break on Windows terminals."""
    width = min(console.width, 80)
    if title:
        pad = max(0, (width - len(title) - 4) // 2)
        line = "-" * pad + f"  {title}  " + "-" * pad
    else:
        line = "-" * width
    text = Text(line)
    text.stylize(style)
    console.print(text)


def _print_pronunciation_hints(
    block: FormattedBlock,
    console: Console,
) -> None:
    """Print pronunciation annotations below a block if any exist."""
    if not block.pronunciation_hints:
        return
    for hint in block.pronunciation_hints:
        indent = "    " * max(block.indent_level, 1)
        hint_text = Text(f"{indent}[{hint}]")
        hint_text.stylize("dim italic")
        console.print(hint_text)


def render_preview(
    blocks: list[FormattedBlock],
    toggles: FormatToggles,
    console: Console,
) -> None:
    """Render a styled terminal approximation of the formatted script.

    Not pixel-perfect to the PDF, but shows structure, colors, and styling
    so the user can verify before generating output.
    """
    for block in blocks:
        _render_block(block, toggles, console)


def _render_block(
    block: FormattedBlock,
    toggles: FormatToggles,
    console: Console,
) -> None:
    """Render a single FormattedBlock to the terminal."""
    bt = block.block_type

    # Page break
    if bt == BlockType.PAGE_BREAK:
        _print_rule(console, "PAGE BREAK", "dim")
        return

    # Blank line
    if bt == BlockType.BLANK_LINE:
        console.print()
        return

    # Section divider
    if bt == BlockType.SECTION_DIVIDER:
        _print_rule(console)
        return

    # Source label open
    if bt == BlockType.SOURCE_LABEL_OPEN:
        console.print()
        _print_rule(console, block.text, "bold cyan")
        return

    # Source label close
    if bt == BlockType.SOURCE_LABEL_CLOSE:
        _print_rule(console, style="dim")
        console.print()
        return

    # Voice batch header
    if bt == BlockType.VOICE_BATCH_HEADER:
        console.print()
        _print_rule(console, block.text, "bold")
        console.print()
        return

    # Section header
    if bt == BlockType.SECTION_HEADER:
        _print_rule(console, block.text, "bold")
        return

    # Character name
    if bt == BlockType.CHARACTER_NAME:
        text = Text(block.text)
        text.stylize(f"bold {block.color}" if block.color != "#000000" else "bold")
        console.print(text)
        _print_pronunciation_hints(block, console)
        return

    # Dialogue
    if bt == BlockType.DIALOGUE:
        indent = "    " * max(block.indent_level, 1)
        text = Text(f"{indent}{block.text}")
        if block.color and block.color != "#000000":
            text.stylize(block.color)
        console.print(text)
        _print_pronunciation_hints(block, console)
        return

    # Stage direction
    if bt == BlockType.STAGE_DIRECTION:
        indent = "    " * max(block.indent_level, 1)
        text = Text(f"{indent}{block.text}")
        text.stylize("italic grey62")
        console.print(text)
        return

    # Sound cue
    if bt == BlockType.SOUND_CUE:
        text = Text(block.text)
        text.stylize("dim")
        console.print(text, justify="center")
        return

    # Narration
    if bt == BlockType.NARRATION:
        indent = "    " * block.indent_level
        text = Text(f"{indent}{block.text}")
        if toggles.narrator_style == NarratorStyle.ITALIC:
            text.stylize("italic")
        elif toggles.narrator_style == NarratorStyle.BOLD:
            text.stylize("bold")
        console.print(text)
        _print_pronunciation_hints(block, console)
        return

    # Prose
    if bt == BlockType.PROSE:
        text = Text(block.text)
        if toggles.narrator_style == NarratorStyle.ITALIC:
            text.stylize("italic")
        elif toggles.narrator_style == NarratorStyle.BOLD:
            text.stylize("bold")
        console.print(text)
        _print_pronunciation_hints(block, console)
        return

    # Quoted text
    if bt == BlockType.QUOTED_TEXT:
        indent = "    " * block.indent_level
        text = Text(f"{indent}{block.text}")
        if block.italic:
            text.stylize("italic")
        console.print(text)
        _print_pronunciation_hints(block, console)
        return

    # Title page title
    if bt == BlockType.TITLE_PAGE_TITLE:
        text = Text(block.text)
        text.stylize("bold")
        console.print(text, justify="center")
        return

    # Title page info
    if bt == BlockType.TITLE_PAGE_INFO:
        indent = "    " * block.indent_level
        text = Text(f"{indent}{block.text}")
        if block.color and block.color != "#000000":
            text.stylize(block.color)
        if block.bold:
            text.stylize("bold")
        console.print(text)
        return

    # Character legend header
    if bt == BlockType.CHARACTER_LEGEND_HEADER:
        text = Text(block.text)
        text.stylize("bold")
        console.print(text, justify="center")
        return

    # Character legend entry
    if bt == BlockType.CHARACTER_LEGEND_ENTRY:
        indent = "    " * block.indent_level
        text = Text(f"{indent}# {block.text}")
        if block.color and block.color != "#000000":
            text.stylize(f"bold {block.color}")
        else:
            text.stylize("bold")
        console.print(text)
        return

    # Fallback: just print the text
    if block.text:
        console.print(block.text)
        _print_pronunciation_hints(block, console)
