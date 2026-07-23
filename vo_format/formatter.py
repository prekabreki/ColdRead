"""Core formatting engine with archetype-specific formatters."""

from __future__ import annotations

import re
from typing import Callable, Optional

from .colors import (
    NARRATOR_COLOR,
    SOUND_CUE_COLOR,
    STAGE_DIRECTION_COLOR,
    assign_colors,
)
from .models import (
    Archetype,
    BlockType,
    FormattedBlock,
    FormatToggles,
    NarratorStyle,
    PreflightResult,
    QuotedTextStyle,
)


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _strip_md_bold(text: str) -> str:
    """Remove markdown bold markers (**text** -> text)."""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text)


def _strip_md_italic(text: str) -> str:
    """Remove markdown italic markers (*text* -> text)."""
    return re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)


def _strip_all_md(text: str) -> str:
    """Strip all common markdown formatting from text, preserving inline emphasis.

    Bold (**text**) and italic (*text*) markers are kept so the PDF writer
    can convert them to <b>/<i> tags.  Structural markdown (links, images,
    header markers) is removed.
    """
    # Links [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Images ![][id] or ![alt](url)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"!\[[^\]]*\]\[[^\]]*\]", "", text)
    # Header markers
    text = re.sub(r"^#{1,6}\s+", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Line pattern regexes
# ---------------------------------------------------------------------------

# **CHARACTER_NAME:** or **CHARACTER NAME:** (bold character with colon)
RE_BOLD_CHARACTER = re.compile(
    r"^\*\*([A-Z][A-Z0-9\s''()\-]+?):\*\*\s*(.*)"
)

# CHARACTER_NAME: (plain text, all-caps name with colon)
RE_PLAIN_CHARACTER = re.compile(
    r"^([A-Z][A-Z0-9\s''()\-]+?):\s*(.*)"
)

# Stage direction: *(text)* or *(text)*  (italic parenthetical)
RE_STAGE_DIRECTION_PAREN = re.compile(r"^\*\((.+?)\)\*$")

# Stage direction: *[text]* (italic bracketed)
RE_STAGE_DIRECTION_BRACKET = re.compile(r"^\*\[(.+?)\]\*$")

# Sound cue: **[TEXT]** (bold bracketed, typically all caps)
RE_SOUND_CUE = re.compile(r"^\*\*\[(.+?)\]\*\*$")

# Section header: ## text or ## [text]
RE_SECTION_HEADER = re.compile(r"^#{2,3}\s+(.+)")

# Horizontal rule: --- or *** or ___ (3+ chars)
RE_HORIZONTAL_RULE = re.compile(r"^[-*_]{3,}\s*$")

# Markdown image reference
RE_IMAGE_REF = re.compile(r"^!\[.*?\].*$")

# Quoted text start: *"text  (italic opening quote)
RE_QUOTED_START = re.compile(r'^\*"(.*)$')

# Quoted text end: text"*  (italic closing quote)
RE_QUOTED_END = re.compile(r'^(.*?)"\*\s*$')

# Single-line quoted text: *"text"*
RE_QUOTED_SINGLE = re.compile(r'^\*"(.+?)"\*\s*$')


# ---------------------------------------------------------------------------
# Source label patterns (plain text from PDF extraction)
# ---------------------------------------------------------------------------

# "Document Archive Section X: Title" (Bloodborne style)
RE_DOC_ARCHIVE_SECTION = re.compile(
    r"^Document Archive Section\s+([A-Z]):\s*(.+)", re.IGNORECASE
)

# "Document Archive X-NNN: Title" (Deus Ex style)
RE_DOC_ARCHIVE_CODE = re.compile(
    r"^Document Archive\s+([A-Z]-?\w+):\s*(.+)", re.IGNORECASE
)

# "Document Reference: CODE-NNN"
RE_DOC_REF = re.compile(r"^Document Reference:\s*(.+)", re.IGNORECASE)

# "FROM: name TO: name" email header
RE_EMAIL_HEADER = re.compile(r"^FROM:\s*(.+)", re.IGNORECASE)

# "News Ticker: text..."
RE_NEWS_TICKER = re.compile(r"^News Ticker:\s*(.+)", re.IGNORECASE)

# "Opening Narrative" / "Closing Narrative"
RE_NARRATIVE_LABEL = re.compile(r"^(Opening|Closing)\s+Narrative\s*$", re.IGNORECASE)

# Plain-text stage direction: [text in brackets] (not all-caps)
RE_PLAIN_STAGE_DIRECTION = re.compile(r"^\[([^\]]+)\]$")

# Plain-text sound cue: [ALL CAPS TEXT] or [Sound of ...]
RE_PLAIN_SOUND_CUE = re.compile(r"^\[(Sound of .+|[A-Z][A-Z\s.,;:'\-]+)\]$")

# ---------------------------------------------------------------------------
# Metadata stripping
# ---------------------------------------------------------------------------

def _strip_metadata_blocks(
    lines: list[str],
    metadata_blocks: list,
) -> list[tuple[int, str]]:
    """Remove lines identified as metadata by preflight.

    Returns list of (original_line_number, text) tuples for surviving lines.
    Line numbers are 1-based matching the original file.
    """
    # Build set of line numbers to skip
    skip_lines: set[int] = set()
    for mb in metadata_blocks:
        for ln in range(mb.start_line, mb.end_line + 1):
            skip_lines.add(ln)

    result = []
    for i, line in enumerate(lines):
        line_num = i + 1  # 1-based
        if line_num not in skip_lines:
            result.append((line_num, line))
    return result


# ---------------------------------------------------------------------------
# Source label matching
# ---------------------------------------------------------------------------

# Each entry: (compiled_regex, source_type_str, label_format_fn)
# label_format_fn takes the match object and returns the display label string.
_HARDCODED_SOURCE_PATTERNS: list[tuple[re.Pattern, str, Callable[[re.Match], str]]] = [
    (RE_DOC_ARCHIVE_SECTION, "document_section", lambda m: f"Document Archive Section {m.group(1)}: {m.group(2)}"),
    (RE_DOC_ARCHIVE_CODE, "document_archive", lambda m: f"Document Archive {m.group(1)}: {m.group(2)}"),
    (RE_EMAIL_HEADER, "email", lambda m: m.group(0).strip()),
    (RE_NEWS_TICKER, "news_ticker", lambda m: "News Ticker"),
    (RE_NARRATIVE_LABEL, "narrative_label", lambda m: m.group(0).strip()),
]


def _compile_source_patterns(
    source_types: list,
) -> list[tuple[re.Pattern, str, Callable[[re.Match], str]]]:
    """Build source label patterns from preflight source_types + hardcoded patterns.

    Returns list of (pattern, source_type_str, label_fn) sorted longest-prefix first.
    """
    patterns = list(_HARDCODED_SOURCE_PATTERNS)

    # Add dynamic patterns from preflight source_types
    for st in source_types:
        if st.prefix and st.prefix.strip():
            prefix = re.escape(st.prefix.strip())
            pat = re.compile(rf"^{prefix}\s*(.+)?", re.IGNORECASE)
            patterns.append((pat, st.type, lambda m, p=st.prefix: p.strip() + (f" {m.group(1)}" if m.group(1) else "")))
        elif st.label and st.label.strip():
            label = re.escape(st.label.strip())
            pat = re.compile(rf"^{label}\s*$", re.IGNORECASE)
            patterns.append((pat, st.type, lambda m, l=st.label: l.strip()))

    return patterns


def _match_source_label(
    line: str,
    patterns: list[tuple[re.Pattern, str, Callable[[re.Match], str]]],
) -> tuple[str, str] | None:
    """Check if a line matches any source label pattern.

    Returns (source_type_str, display_label) or None.
    """
    for pat, source_type, label_fn in patterns:
        m = pat.match(line)
        if m:
            return (source_type, label_fn(m))
    return None


# ---------------------------------------------------------------------------
# Archetype formatters
# ---------------------------------------------------------------------------

def _get_character_color(
    name: str,
    color_map: dict[str, str],
    toggles: FormatToggles,
) -> str:
    """Get the display color for a character."""
    if not toggles.color_characters:
        return NARRATOR_COLOR
    return color_map.get(name, NARRATOR_COLOR)


def _make_narrator_block(
    text: str,
    toggles: FormatToggles,
    source_line: int | None = None,
    match_pattern: str = "FALLBACK_NARRATION",
) -> FormattedBlock:
    """Create a narration block with the configured narrator style."""
    return FormattedBlock(
        block_type=BlockType.NARRATION,
        text=_strip_all_md(text),
        color=NARRATOR_COLOR,
        bold=toggles.narrator_style == NarratorStyle.BOLD,
        italic=toggles.narrator_style == NarratorStyle.ITALIC,
        indent_level=1,
        source_line=source_line,
        match_pattern=match_pattern,
    )


def _format_multi_voice_drama(
    numbered_lines: list[tuple[int, str]],
    preflight: PreflightResult,
    toggles: FormatToggles,
    color_map: dict[str, str],
) -> list[FormattedBlock]:
    """Format a multi-voice drama script (Warcraft, Warhammer style)."""
    blocks: list[FormattedBlock] = []
    known_names = {c.name.upper(): c.name for c in preflight.characters}
    current_speaker: Optional[str] = None
    first_section = True

    for line_num, raw_line in numbered_lines:
        line = raw_line.strip()

        # Blank line
        if not line:
            blocks.append(FormattedBlock(
                block_type=BlockType.BLANK_LINE,
                text="",
                source_line=line_num,
            ))
            continue

        # Image reference — skip
        if RE_IMAGE_REF.match(line):
            continue

        # Horizontal rule
        if RE_HORIZONTAL_RULE.match(line):
            blocks.append(FormattedBlock(
                block_type=BlockType.SECTION_DIVIDER,
                text="",
                source_line=line_num,
                match_pattern="RE_HORIZONTAL_RULE",
            ))
            current_speaker = None
            continue

        # Section header
        m = RE_SECTION_HEADER.match(line)
        if m:
            title = m.group(1).strip()
            # Remove surrounding brackets if present
            if title.startswith("[") and title.endswith("]"):
                title = title[1:-1]
            block = FormattedBlock(
                block_type=BlockType.SECTION_HEADER,
                text=title,
                color=NARRATOR_COLOR,
                bold=True,
                is_centered=True,
                font_size_override=(toggles.font_size + 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_SECTION_HEADER",
            )
            if toggles.section_breaks and not first_section:
                block.page_break_before = True
            first_section = False
            blocks.append(block)
            current_speaker = None
            continue

        # Sound cue: **[TEXT]**
        m = RE_SOUND_CUE.match(line)
        if m and toggles.sound_cues:
            blocks.append(FormattedBlock(
                block_type=BlockType.SOUND_CUE,
                text=f"[{m.group(1)}]",
                color=SOUND_CUE_COLOR,
                italic=False,
                is_centered=True,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_SOUND_CUE",
            ))
            continue
        elif m and not toggles.sound_cues:
            continue

        # Stage direction (italic parenthetical): *(text)*
        m = RE_STAGE_DIRECTION_PAREN.match(line)
        if m and toggles.stage_directions:
            blocks.append(FormattedBlock(
                block_type=BlockType.STAGE_DIRECTION,
                text=f"({m.group(1)})",
                color=STAGE_DIRECTION_COLOR,
                italic=True,
                indent_level=1,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_STAGE_DIRECTION_PAREN",
                speaker=current_speaker,
            ))
            continue
        elif m and not toggles.stage_directions:
            continue

        # Stage direction (italic bracketed): *[text]*
        m = RE_STAGE_DIRECTION_BRACKET.match(line)
        if m and toggles.stage_directions:
            blocks.append(FormattedBlock(
                block_type=BlockType.STAGE_DIRECTION,
                text=f"[{m.group(1)}]",
                color=STAGE_DIRECTION_COLOR,
                italic=True,
                indent_level=1,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_STAGE_DIRECTION_BRACKET",
                speaker=current_speaker,
            ))
            continue
        elif m and not toggles.stage_directions:
            continue

        # Bold character name: **NAME:**
        m = RE_BOLD_CHARACTER.match(line)
        if m:
            raw_name = m.group(1).strip()
            remainder = m.group(2).strip()
            # Look up in known characters (case-insensitive)
            canonical_name = known_names.get(raw_name.upper(), raw_name)
            char_color = _get_character_color(canonical_name, color_map, toggles)
            current_speaker = canonical_name

            blocks.append(FormattedBlock(
                block_type=BlockType.CHARACTER_NAME,
                text=f"{canonical_name}:",
                color=char_color,
                bold=True,
                keep_with_next=True,
                source_line=line_num,
                match_pattern="RE_BOLD_CHARACTER",
                speaker=canonical_name,
            ))

            # If there's dialogue on the same line
            if remainder:
                blocks.append(FormattedBlock(
                    block_type=BlockType.DIALOGUE,
                    text=_strip_all_md(remainder),
                    color=char_color,
                    indent_level=1,
                    source_line=line_num,
                    match_pattern="RE_BOLD_CHARACTER",
                    speaker=canonical_name,
                ))
            continue

        # Plain character name: NAME: (only if name is in known characters)
        m = RE_PLAIN_CHARACTER.match(line)
        if m:
            raw_name = m.group(1).strip()
            if raw_name.upper() in known_names:
                remainder = m.group(2).strip()
                canonical_name = known_names[raw_name.upper()]
                char_color = _get_character_color(canonical_name, color_map, toggles)
                current_speaker = canonical_name

                blocks.append(FormattedBlock(
                    block_type=BlockType.CHARACTER_NAME,
                    text=f"{canonical_name}:",
                    color=char_color,
                    bold=True,
                    keep_with_next=True,
                    source_line=line_num,
                    match_pattern="RE_PLAIN_CHARACTER",
                    speaker=canonical_name,
                ))

                if remainder:
                    blocks.append(FormattedBlock(
                        block_type=BlockType.DIALOGUE,
                        text=_strip_all_md(remainder),
                        color=char_color,
                        indent_level=1,
                        source_line=line_num,
                        match_pattern="RE_PLAIN_CHARACTER",
                        speaker=canonical_name,
                    ))
                continue

        # Default: dialogue or narration
        cleaned = _strip_all_md(line)
        if current_speaker:
            char_color = _get_character_color(current_speaker, color_map, toggles)
            blocks.append(FormattedBlock(
                block_type=BlockType.DIALOGUE,
                text=cleaned,
                color=char_color,
                indent_level=1,
                source_line=line_num,
                match_pattern="FALLBACK_DIALOGUE",
                speaker=current_speaker,
            ))
        else:
            blocks.append(_make_narrator_block(line, toggles, source_line=line_num))

    return blocks


def _format_single_narrator(
    numbered_lines: list[tuple[int, str]],
    preflight: PreflightResult,
    toggles: FormatToggles,
    color_map: dict[str, str],
) -> list[FormattedBlock]:
    """Format a single-narrator script (FFXII style)."""
    blocks: list[FormattedBlock] = []
    known_names = {c.name.upper(): c.name for c in preflight.characters}
    current_speaker: Optional[str] = None
    in_quoted_block = False
    first_section = True

    for line_num, raw_line in numbered_lines:
        line = raw_line.strip()

        # Blank line
        if not line:
            blocks.append(FormattedBlock(
                block_type=BlockType.BLANK_LINE,
                text="",
                source_line=line_num,
            ))
            if in_quoted_block:
                # Blank line within a quoted block is fine — quotes can span paragraphs
                pass
            continue

        # Image reference — skip
        if RE_IMAGE_REF.match(line):
            continue

        # Horizontal rule
        if RE_HORIZONTAL_RULE.match(line):
            # End quoted block if one was open
            in_quoted_block = False
            blocks.append(FormattedBlock(
                block_type=BlockType.SECTION_DIVIDER,
                text="",
                source_line=line_num,
                match_pattern="RE_HORIZONTAL_RULE",
            ))
            current_speaker = None
            continue

        # Section header
        m = RE_SECTION_HEADER.match(line)
        if m:
            in_quoted_block = False
            title = m.group(1).strip()
            if title.startswith("[") and title.endswith("]"):
                title = title[1:-1]
            block = FormattedBlock(
                block_type=BlockType.SECTION_HEADER,
                text=title,
                color=NARRATOR_COLOR,
                bold=True,
                is_centered=True,
                font_size_override=(toggles.font_size + 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_SECTION_HEADER",
            )
            if toggles.section_breaks and not first_section:
                block.page_break_before = True
            first_section = False
            blocks.append(block)
            current_speaker = None
            continue

        # Sound cue
        m = RE_SOUND_CUE.match(line)
        if m:
            if toggles.sound_cues:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOUND_CUE,
                    text=f"[{m.group(1)}]",
                    color=SOUND_CUE_COLOR,
                    is_centered=True,
                    font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                    source_line=line_num,
                    match_pattern="RE_SOUND_CUE",
                ))
            continue

        # Stage direction (parenthetical)
        m = RE_STAGE_DIRECTION_PAREN.match(line)
        if m:
            if toggles.stage_directions:
                blocks.append(FormattedBlock(
                    block_type=BlockType.STAGE_DIRECTION,
                    text=f"({m.group(1)})",
                    color=STAGE_DIRECTION_COLOR,
                    italic=True,
                    indent_level=1,
                    font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                    source_line=line_num,
                    match_pattern="RE_STAGE_DIRECTION_PAREN",
                    speaker=current_speaker,
                ))
            continue

        # Stage direction (bracketed)
        m = RE_STAGE_DIRECTION_BRACKET.match(line)
        if m:
            if toggles.stage_directions:
                blocks.append(FormattedBlock(
                    block_type=BlockType.STAGE_DIRECTION,
                    text=f"[{m.group(1)}]",
                    color=STAGE_DIRECTION_COLOR,
                    italic=True,
                    indent_level=1,
                    font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                    source_line=line_num,
                    match_pattern="RE_STAGE_DIRECTION_BRACKET",
                    speaker=current_speaker,
                ))
            continue

        # Bold character name
        m = RE_BOLD_CHARACTER.match(line)
        if m:
            in_quoted_block = False
            raw_name = m.group(1).strip()
            remainder = m.group(2).strip()
            canonical_name = known_names.get(raw_name.upper(), raw_name)
            char_color = _get_character_color(canonical_name, color_map, toggles)
            current_speaker = canonical_name

            blocks.append(FormattedBlock(
                block_type=BlockType.CHARACTER_NAME,
                text=f"{canonical_name}:",
                color=char_color,
                bold=True,
                keep_with_next=True,
                source_line=line_num,
                match_pattern="RE_BOLD_CHARACTER",
                speaker=canonical_name,
            ))

            if remainder:
                blocks.append(FormattedBlock(
                    block_type=BlockType.DIALOGUE,
                    text=_strip_all_md(remainder),
                    color=char_color,
                    indent_level=1,
                    source_line=line_num,
                    match_pattern="RE_BOLD_CHARACTER",
                    speaker=canonical_name,
                ))
            continue

        # Single-line quoted text: *"text"*
        m = RE_QUOTED_SINGLE.match(line)
        if m:
            text = m.group(1)
            indent, italic = _quoted_style(toggles)
            blocks.append(FormattedBlock(
                block_type=BlockType.QUOTED_TEXT,
                text=f'"{text}"',
                color=NARRATOR_COLOR,
                italic=italic,
                indent_level=indent,
                source_line=line_num,
                match_pattern="RE_QUOTED_SINGLE",
            ))
            in_quoted_block = False
            continue

        # Start of multi-line quoted block: *"text
        m = RE_QUOTED_START.match(line)
        if m:
            text = m.group(1)
            indent, italic = _quoted_style(toggles)
            blocks.append(FormattedBlock(
                block_type=BlockType.QUOTED_TEXT,
                text=f'"{text}',
                color=NARRATOR_COLOR,
                italic=italic,
                indent_level=indent,
                source_line=line_num,
                match_pattern="RE_QUOTED_START",
            ))
            in_quoted_block = True
            continue

        # End of multi-line quoted block: text"*
        if in_quoted_block:
            m = RE_QUOTED_END.match(line)
            if m:
                text = m.group(1)
                indent, italic = _quoted_style(toggles)
                blocks.append(FormattedBlock(
                    block_type=BlockType.QUOTED_TEXT,
                    text=f'{text}"',
                    color=NARRATOR_COLOR,
                    italic=italic,
                    indent_level=indent,
                    source_line=line_num,
                    match_pattern="RE_QUOTED_END",
                ))
                in_quoted_block = False
                continue
            else:
                # Continuation of quoted block
                indent, italic = _quoted_style(toggles)
                # Strip italic markers if present
                cleaned = _strip_md_italic(line)
                blocks.append(FormattedBlock(
                    block_type=BlockType.QUOTED_TEXT,
                    text=cleaned,
                    color=NARRATOR_COLOR,
                    italic=italic,
                    indent_level=indent,
                    source_line=line_num,
                    match_pattern="RE_QUOTED_BLOCK",
                ))
                continue

        # Default: narration
        blocks.append(_make_narrator_block(line, toggles, source_line=line_num))

    return blocks


def _quoted_style(toggles: FormatToggles) -> tuple[int, bool]:
    """Return (indent_level, italic) based on quoted_text_style toggle."""
    style = toggles.quoted_text_style
    if style == QuotedTextStyle.INDENT:
        return 2, False
    elif style == QuotedTextStyle.ITALIC:
        return 1, True
    elif style == QuotedTextStyle.INDENT_ITALIC:
        return 2, True
    else:  # NONE
        return 1, False


def _format_continuous_prose(
    numbered_lines: list[tuple[int, str]],
    preflight: PreflightResult,
    toggles: FormatToggles,
    color_map: dict[str, str],
) -> list[FormattedBlock]:
    """Format a continuous prose script (Kingdom Hearts style)."""
    blocks: list[FormattedBlock] = []
    first_section = True

    for line_num, raw_line in numbered_lines:
        line = raw_line.strip()

        # Blank line -> paragraph separator
        if not line:
            blocks.append(FormattedBlock(
                block_type=BlockType.BLANK_LINE,
                text="",
                source_line=line_num,
            ))
            continue

        # Image reference — skip
        if RE_IMAGE_REF.match(line):
            continue

        # Horizontal rule
        if RE_HORIZONTAL_RULE.match(line):
            blocks.append(FormattedBlock(
                block_type=BlockType.SECTION_DIVIDER,
                text="",
                source_line=line_num,
                match_pattern="RE_HORIZONTAL_RULE",
            ))
            continue

        # Section header
        m = RE_SECTION_HEADER.match(line)
        if m:
            title = m.group(1).strip()
            if title.startswith("[") and title.endswith("]"):
                title = title[1:-1]
            block = FormattedBlock(
                block_type=BlockType.SECTION_HEADER,
                text=title,
                color=NARRATOR_COLOR,
                bold=True,
                is_centered=True,
                font_size_override=(toggles.font_size + 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_SECTION_HEADER",
            )
            if toggles.section_breaks and not first_section:
                block.page_break_before = True
            first_section = False
            blocks.append(block)
            continue

        # Prose paragraph line
        cleaned = _strip_all_md(line)
        if cleaned:
            blocks.append(FormattedBlock(
                block_type=BlockType.PROSE,
                text=cleaned,
                color=NARRATOR_COLOR,
                source_line=line_num,
                match_pattern="FALLBACK_PROSE",
            ))

    return blocks


def _format_document_archive(
    numbered_lines: list[tuple[int, str]],
    preflight: PreflightResult,
    toggles: FormatToggles,
    color_map: dict[str, str],
) -> list[FormattedBlock]:
    """Format a document archive script (Bloodborne style).

    When source_labels is enabled, wraps content in SOURCE_LABEL_OPEN/CLOSE
    containers around detected source label boundaries. Interior content is
    classified as narration, sound cues, or stage directions.
    """
    if not toggles.source_labels:
        return _format_single_narrator(numbered_lines, preflight, toggles, color_map)

    blocks: list[FormattedBlock] = []
    source_patterns = _compile_source_patterns(preflight.source_types)
    in_source_block = False
    first_section = True

    for line_num, raw_line in numbered_lines:
        line = raw_line.strip()

        # Blank line
        if not line:
            blocks.append(FormattedBlock(
                block_type=BlockType.BLANK_LINE,
                text="",
                source_line=line_num,
            ))
            continue

        # Image reference — skip
        if RE_IMAGE_REF.match(line):
            continue

        # Horizontal rule
        if RE_HORIZONTAL_RULE.match(line):
            blocks.append(FormattedBlock(
                block_type=BlockType.SECTION_DIVIDER,
                text="",
                source_line=line_num,
                match_pattern="RE_HORIZONTAL_RULE",
            ))
            continue

        # Markdown section header (## text)
        m = RE_SECTION_HEADER.match(line)
        if m:
            if in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOURCE_LABEL_CLOSE,
                    text="",
                    source_line=line_num,
                ))
                in_source_block = False
            title = m.group(1).strip()
            if title.startswith("[") and title.endswith("]"):
                title = title[1:-1]
            block = FormattedBlock(
                block_type=BlockType.SECTION_HEADER,
                text=title,
                color=NARRATOR_COLOR,
                bold=True,
                is_centered=True,
                font_size_override=(toggles.font_size + 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_SECTION_HEADER",
            )
            if toggles.section_breaks and not first_section:
                block.page_break_before = True
            first_section = False
            blocks.append(block)
            continue

        # Check for source label match
        match_result = _match_source_label(line, source_patterns)

        # Document Reference opens a sub-label within a section
        doc_ref_m = RE_DOC_REF.match(line)
        if doc_ref_m:
            # Close previous source block if it was a doc ref
            if in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOURCE_LABEL_CLOSE,
                    text="",
                    source_line=line_num,
                ))
            blocks.append(FormattedBlock(
                block_type=BlockType.SOURCE_LABEL_OPEN,
                text=f"Document Reference: {doc_ref_m.group(1).strip()}",
                source_type="document_ref",
                bold=True,
                is_centered=True,
                source_line=line_num,
                match_pattern="RE_DOC_REF",
            ))
            in_source_block = True
            continue

        # Document Archive Section header (major section divider)
        if match_result and match_result[0] == "document_section":
            if in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOURCE_LABEL_CLOSE,
                    text="",
                    source_line=line_num,
                ))
                in_source_block = False
            source_type, label = match_result
            block = FormattedBlock(
                block_type=BlockType.SECTION_HEADER,
                text=label,
                color=NARRATOR_COLOR,
                bold=True,
                is_centered=True,
                font_size_override=(toggles.font_size + 2) if toggles.font_size else None,
                source_line=line_num,
                source_type=source_type,
                match_pattern="RE_SOURCE_LABEL",
            )
            if toggles.section_breaks and not first_section:
                block.page_break_before = True
            first_section = False
            blocks.append(block)
            continue

        # Narrative label (Opening/Closing Narrative)
        if match_result and match_result[0] == "narrative_label":
            if in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOURCE_LABEL_CLOSE,
                    text="",
                    source_line=line_num,
                ))
                in_source_block = False
            _, label = match_result
            block = FormattedBlock(
                block_type=BlockType.SECTION_HEADER,
                text=label,
                color=NARRATOR_COLOR,
                bold=True,
                is_centered=True,
                source_line=line_num,
                match_pattern="RE_SOURCE_LABEL",
            )
            if toggles.section_breaks and not first_section:
                block.page_break_before = True
            first_section = False
            blocks.append(block)
            continue

        # Other source label match (if not already handled above)
        if match_result and match_result[0] not in ("document_section", "narrative_label"):
            if in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOURCE_LABEL_CLOSE,
                    text="",
                    source_line=line_num,
                ))
            source_type, label = match_result
            blocks.append(FormattedBlock(
                block_type=BlockType.SOURCE_LABEL_OPEN,
                text=label,
                source_type=source_type,
                bold=True,
                is_centered=True,
                source_line=line_num,
                match_pattern="RE_SOURCE_LABEL",
            ))
            in_source_block = True
            continue

        # Plain-text sound cue: [Sound of ...] or [ALL CAPS TEXT]
        m = RE_PLAIN_SOUND_CUE.match(line)
        if m and toggles.sound_cues:
            blocks.append(FormattedBlock(
                block_type=BlockType.SOUND_CUE,
                text=f"[{m.group(1)}]" if not line.startswith("[") else line,
                color=SOUND_CUE_COLOR,
                is_centered=True,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_PLAIN_SOUND_CUE",
            ))
            continue
        elif m and not toggles.sound_cues:
            continue

        # Plain-text stage direction: [text in brackets]
        m = RE_PLAIN_STAGE_DIRECTION.match(line)
        if m and toggles.stage_directions:
            blocks.append(FormattedBlock(
                block_type=BlockType.STAGE_DIRECTION,
                text=line,
                color=STAGE_DIRECTION_COLOR,
                italic=True,
                indent_level=1,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_PLAIN_STAGE_DIRECTION",
            ))
            continue
        elif m and not toggles.stage_directions:
            continue

        # Markdown sound cue: **[TEXT]**
        m = RE_SOUND_CUE.match(line)
        if m and toggles.sound_cues:
            blocks.append(FormattedBlock(
                block_type=BlockType.SOUND_CUE,
                text=f"[{m.group(1)}]",
                color=SOUND_CUE_COLOR,
                is_centered=True,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_SOUND_CUE",
            ))
            continue
        elif m and not toggles.sound_cues:
            continue

        # Markdown stage directions
        m = RE_STAGE_DIRECTION_PAREN.match(line)
        if m and toggles.stage_directions:
            blocks.append(FormattedBlock(
                block_type=BlockType.STAGE_DIRECTION,
                text=f"({m.group(1)})",
                color=STAGE_DIRECTION_COLOR,
                italic=True,
                indent_level=1,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_STAGE_DIRECTION_PAREN",
            ))
            continue
        elif m and not toggles.stage_directions:
            continue

        m = RE_STAGE_DIRECTION_BRACKET.match(line)
        if m and toggles.stage_directions:
            blocks.append(FormattedBlock(
                block_type=BlockType.STAGE_DIRECTION,
                text=f"[{m.group(1)}]",
                color=STAGE_DIRECTION_COLOR,
                italic=True,
                indent_level=1,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_STAGE_DIRECTION_BRACKET",
            ))
            continue
        elif m and not toggles.stage_directions:
            continue

        # Default: narration (indented if inside a source block)
        cleaned = _strip_all_md(line)
        if cleaned:
            block = _make_narrator_block(line, toggles, source_line=line_num)
            if in_source_block:
                block.indent_level = 1
            blocks.append(block)

    # Close any open source block
    if in_source_block:
        blocks.append(FormattedBlock(
            block_type=BlockType.SOURCE_LABEL_CLOSE,
            text="",
        ))

    return blocks


def _format_mixed_media(
    numbered_lines: list[tuple[int, str]],
    preflight: PreflightResult,
    toggles: FormatToggles,
    color_map: dict[str, str],
) -> list[FormattedBlock]:
    """Format a mixed media script (Deus Ex style).

    When source_labels is enabled, wraps content in SOURCE_LABEL_OPEN/CLOSE
    containers. Interior content uses multi-voice classification (character
    names with colors, dialogue, stage directions).
    """
    if not toggles.source_labels:
        return _format_multi_voice_drama(numbered_lines, preflight, toggles, color_map)

    blocks: list[FormattedBlock] = []
    source_patterns = _compile_source_patterns(preflight.source_types)
    known_names = {c.name.upper(): c.name for c in preflight.characters}
    current_speaker: Optional[str] = None
    in_source_block = False
    first_section = True

    for line_num, raw_line in numbered_lines:
        line = raw_line.strip()

        # Blank line
        if not line:
            blocks.append(FormattedBlock(
                block_type=BlockType.BLANK_LINE,
                text="",
                source_line=line_num,
            ))
            continue

        # Image reference — skip
        if RE_IMAGE_REF.match(line):
            continue

        # Horizontal rule
        if RE_HORIZONTAL_RULE.match(line):
            blocks.append(FormattedBlock(
                block_type=BlockType.SECTION_DIVIDER,
                text="",
                source_line=line_num,
                match_pattern="RE_HORIZONTAL_RULE",
            ))
            current_speaker = None
            continue

        # Markdown section header
        m = RE_SECTION_HEADER.match(line)
        if m:
            if in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOURCE_LABEL_CLOSE,
                    text="",
                    source_line=line_num,
                ))
                in_source_block = False
            title = m.group(1).strip()
            if title.startswith("[") and title.endswith("]"):
                title = title[1:-1]
            block = FormattedBlock(
                block_type=BlockType.SECTION_HEADER,
                text=title,
                color=NARRATOR_COLOR,
                bold=True,
                is_centered=True,
                font_size_override=(toggles.font_size + 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_SECTION_HEADER",
            )
            if toggles.section_breaks and not first_section:
                block.page_break_before = True
            first_section = False
            blocks.append(block)
            current_speaker = None
            continue

        # Check for source label match
        match_result = _match_source_label(line, source_patterns)

        # Document Archive headers act as section dividers
        if match_result and match_result[0] in ("document_archive", "document_section"):
            if in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOURCE_LABEL_CLOSE,
                    text="",
                    source_line=line_num,
                ))
            source_type, label = match_result
            block = FormattedBlock(
                block_type=BlockType.SOURCE_LABEL_OPEN,
                text=label,
                source_type=source_type,
                bold=True,
                is_centered=True,
                source_line=line_num,
                match_pattern="RE_SOURCE_LABEL",
            )
            if toggles.section_breaks and not first_section:
                block.page_break_before = True
            first_section = False
            in_source_block = True
            blocks.append(block)
            current_speaker = None
            continue

        # Other source labels (broadcasts, transmissions, emails, etc.)
        if match_result and match_result[0] not in ("document_archive", "document_section"):
            source_type, label = match_result

            # News tickers are content, not containers
            if source_type == "news_ticker":
                blocks.append(FormattedBlock(
                    block_type=BlockType.NARRATION,
                    text=line,
                    color=NARRATOR_COLOR,
                    italic=True,
                    indent_level=1,
                    font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                    source_line=line_num,
                    match_pattern="RE_SOURCE_LABEL",
                ))
                continue

            # Emails within an existing source block: render as a styled
            # sub-header, not a container (no open/close nesting)
            if source_type == "email" and in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.BLANK_LINE,
                    text="",
                ))
                blocks.append(FormattedBlock(
                    block_type=BlockType.NARRATION,
                    text=label,
                    color=NARRATOR_COLOR,
                    bold=True,
                    indent_level=0,
                    source_line=line_num,
                    source_type=source_type,
                    match_pattern="RE_SOURCE_LABEL",
                ))
                current_speaker = None
                continue

            # Other source labels open a new container
            if in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOURCE_LABEL_CLOSE,
                    text="",
                    source_line=line_num,
                ))
            blocks.append(FormattedBlock(
                block_type=BlockType.SOURCE_LABEL_OPEN,
                text=label,
                source_type=source_type,
                bold=True,
                is_centered=True,
                source_line=line_num,
                match_pattern="RE_SOURCE_LABEL",
            ))
            in_source_block = True
            current_speaker = None
            continue

        # Narrative label
        narrative_m = RE_NARRATIVE_LABEL.match(line)
        if narrative_m:
            if in_source_block:
                blocks.append(FormattedBlock(
                    block_type=BlockType.SOURCE_LABEL_CLOSE,
                    text="",
                    source_line=line_num,
                ))
                in_source_block = False
            block = FormattedBlock(
                block_type=BlockType.SECTION_HEADER,
                text=line,
                color=NARRATOR_COLOR,
                bold=True,
                is_centered=True,
                source_line=line_num,
                match_pattern="RE_NARRATIVE_LABEL",
            )
            if toggles.section_breaks and not first_section:
                block.page_break_before = True
            first_section = False
            blocks.append(block)
            current_speaker = None
            continue

        # Markdown sound cue: **[TEXT]**
        m = RE_SOUND_CUE.match(line)
        if m and toggles.sound_cues:
            blocks.append(FormattedBlock(
                block_type=BlockType.SOUND_CUE,
                text=f"[{m.group(1)}]",
                color=SOUND_CUE_COLOR,
                is_centered=True,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_SOUND_CUE",
            ))
            continue
        elif m and not toggles.sound_cues:
            continue

        # Plain-text sound cue
        m = RE_PLAIN_SOUND_CUE.match(line)
        if m and toggles.sound_cues:
            blocks.append(FormattedBlock(
                block_type=BlockType.SOUND_CUE,
                text=line,
                color=SOUND_CUE_COLOR,
                is_centered=True,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_PLAIN_SOUND_CUE",
            ))
            continue
        elif m and not toggles.sound_cues:
            continue

        # Stage direction (markdown italic parenthetical)
        m = RE_STAGE_DIRECTION_PAREN.match(line)
        if m and toggles.stage_directions:
            blocks.append(FormattedBlock(
                block_type=BlockType.STAGE_DIRECTION,
                text=f"({m.group(1)})",
                color=STAGE_DIRECTION_COLOR,
                italic=True,
                indent_level=1,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_STAGE_DIRECTION_PAREN",
                speaker=current_speaker,
            ))
            continue
        elif m and not toggles.stage_directions:
            continue

        # Stage direction (markdown italic bracketed)
        m = RE_STAGE_DIRECTION_BRACKET.match(line)
        if m and toggles.stage_directions:
            blocks.append(FormattedBlock(
                block_type=BlockType.STAGE_DIRECTION,
                text=f"[{m.group(1)}]",
                color=STAGE_DIRECTION_COLOR,
                italic=True,
                indent_level=1,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_STAGE_DIRECTION_BRACKET",
                speaker=current_speaker,
            ))
            continue
        elif m and not toggles.stage_directions:
            continue

        # Plain-text stage direction
        m = RE_PLAIN_STAGE_DIRECTION.match(line)
        if m and toggles.stage_directions:
            blocks.append(FormattedBlock(
                block_type=BlockType.STAGE_DIRECTION,
                text=line,
                color=STAGE_DIRECTION_COLOR,
                italic=True,
                indent_level=1,
                font_size_override=(toggles.font_size - 2) if toggles.font_size else None,
                source_line=line_num,
                match_pattern="RE_PLAIN_STAGE_DIRECTION",
                speaker=current_speaker,
            ))
            continue
        elif m and not toggles.stage_directions:
            continue

        # Bold character name: **NAME:**
        m = RE_BOLD_CHARACTER.match(line)
        if m:
            raw_name = m.group(1).strip()
            remainder = m.group(2).strip()
            canonical_name = known_names.get(raw_name.upper(), raw_name)
            char_color = _get_character_color(canonical_name, color_map, toggles)
            current_speaker = canonical_name

            blocks.append(FormattedBlock(
                block_type=BlockType.CHARACTER_NAME,
                text=f"{canonical_name}:",
                color=char_color,
                bold=True,
                keep_with_next=True,
                source_line=line_num,
                match_pattern="RE_BOLD_CHARACTER",
                speaker=canonical_name,
            ))
            if remainder:
                blocks.append(FormattedBlock(
                    block_type=BlockType.DIALOGUE,
                    text=_strip_all_md(remainder),
                    color=char_color,
                    indent_level=1,
                    source_line=line_num,
                    match_pattern="RE_BOLD_CHARACTER",
                    speaker=canonical_name,
                ))
            continue

        # Plain character name: NAME: (only known characters)
        m = RE_PLAIN_CHARACTER.match(line)
        if m:
            raw_name = m.group(1).strip()
            if raw_name.upper() in known_names:
                remainder = m.group(2).strip()
                canonical_name = known_names[raw_name.upper()]
                char_color = _get_character_color(canonical_name, color_map, toggles)
                current_speaker = canonical_name

                blocks.append(FormattedBlock(
                    block_type=BlockType.CHARACTER_NAME,
                    text=f"{canonical_name}:",
                    color=char_color,
                    bold=True,
                    keep_with_next=True,
                    source_line=line_num,
                    match_pattern="RE_PLAIN_CHARACTER",
                    speaker=canonical_name,
                ))
                if remainder:
                    blocks.append(FormattedBlock(
                        block_type=BlockType.DIALOGUE,
                        text=_strip_all_md(remainder),
                        color=char_color,
                        indent_level=1,
                        source_line=line_num,
                        match_pattern="RE_PLAIN_CHARACTER",
                        speaker=canonical_name,
                    ))
                continue

        # Default: dialogue or narration
        cleaned = _strip_all_md(line)
        if current_speaker:
            char_color = _get_character_color(current_speaker, color_map, toggles)
            blocks.append(FormattedBlock(
                block_type=BlockType.DIALOGUE,
                text=cleaned,
                color=char_color,
                indent_level=1,
                source_line=line_num,
                match_pattern="FALLBACK_DIALOGUE",
                speaker=current_speaker,
            ))
        else:
            block = _make_narrator_block(line, toggles, source_line=line_num)
            if in_source_block:
                block.indent_level = 1
            blocks.append(block)

    # Close any open source block
    if in_source_block:
        blocks.append(FormattedBlock(
            block_type=BlockType.SOURCE_LABEL_CLOSE,
            text="",
        ))

    return blocks


# ---------------------------------------------------------------------------
# Title page and character legend
# ---------------------------------------------------------------------------

def _build_title_page(
    title: str,
    preflight: PreflightResult,
    color_map: dict[str, str],
    toggles: FormatToggles,
) -> list[FormattedBlock]:
    """Build title page blocks."""
    blocks: list[FormattedBlock] = []

    # Title
    blocks.append(FormattedBlock(
        block_type=BlockType.SECTION_DIVIDER,
        text="",
    ))
    blocks.append(FormattedBlock(
        block_type=BlockType.BLANK_LINE,
        text="",
    ))
    blocks.append(FormattedBlock(
        block_type=BlockType.TITLE_PAGE_TITLE,
        text=title,
        bold=True,
        is_centered=True,
        font_size_override=(toggles.font_size + 6) if toggles.font_size else 22,
    ))
    blocks.append(FormattedBlock(
        block_type=BlockType.BLANK_LINE,
        text="",
    ))
    blocks.append(FormattedBlock(
        block_type=BlockType.SECTION_DIVIDER,
        text="",
    ))
    blocks.append(FormattedBlock(
        block_type=BlockType.BLANK_LINE,
        text="",
    ))

    # Character list
    if preflight.characters:
        blocks.append(FormattedBlock(
            block_type=BlockType.TITLE_PAGE_INFO,
            text="Characters:",
            bold=True,
            indent_level=1,
        ))
        for char in preflight.characters:
            color = color_map.get(char.name, NARRATOR_COLOR)
            blocks.append(FormattedBlock(
                block_type=BlockType.TITLE_PAGE_INFO,
                text=f"  {char.name}",
                color=color,
                indent_level=1,
            ))
        blocks.append(FormattedBlock(
            block_type=BlockType.BLANK_LINE,
            text="",
        ))

    # Archetype info
    blocks.append(FormattedBlock(
        block_type=BlockType.TITLE_PAGE_INFO,
        text=f"Script type: {preflight.archetype.value.replace('_', ' ').title()}",
        indent_level=1,
    ))

    blocks.append(FormattedBlock(
        block_type=BlockType.BLANK_LINE,
        text="",
    ))
    blocks.append(FormattedBlock(
        block_type=BlockType.SECTION_DIVIDER,
        text="",
    ))

    # Page break after title page
    blocks.append(FormattedBlock(
        block_type=BlockType.PAGE_BREAK,
        text="",
    ))

    return blocks


def _build_character_legend(
    preflight: PreflightResult,
    color_map: dict[str, str],
) -> list[FormattedBlock]:
    """Build character legend blocks."""
    blocks: list[FormattedBlock] = []

    blocks.append(FormattedBlock(
        block_type=BlockType.CHARACTER_LEGEND_HEADER,
        text="CHARACTER LEGEND",
        bold=True,
        is_centered=True,
    ))
    blocks.append(FormattedBlock(
        block_type=BlockType.BLANK_LINE,
        text="",
    ))

    # Narrator first
    if preflight.has_narrator:
        blocks.append(FormattedBlock(
            block_type=BlockType.CHARACTER_LEGEND_ENTRY,
            text="Narrator",
            color=NARRATOR_COLOR,
            bold=True,
            indent_level=1,
        ))

    # Other characters sorted by line count
    sorted_chars = sorted(preflight.characters, key=lambda c: c.line_count, reverse=True)
    for char in sorted_chars:
        if char.name.strip().lower() in {"narrator", "the narrator"}:
            continue
        color = color_map.get(char.name, NARRATOR_COLOR)
        blocks.append(FormattedBlock(
            block_type=BlockType.CHARACTER_LEGEND_ENTRY,
            text=f"{char.name} ({char.line_count} lines)",
            color=color,
            bold=True,
            indent_level=1,
        ))

    blocks.append(FormattedBlock(
        block_type=BlockType.BLANK_LINE,
        text="",
    ))
    blocks.append(FormattedBlock(
        block_type=BlockType.SECTION_DIVIDER,
        text="",
    ))
    blocks.append(FormattedBlock(
        block_type=BlockType.BLANK_LINE,
        text="",
    ))

    return blocks


# ---------------------------------------------------------------------------
# Post-processing: breathing marks and pause notation
# ---------------------------------------------------------------------------

# Block types whose text content should be processed
_TEXT_BLOCK_TYPES = {
    BlockType.DIALOGUE,
    BlockType.NARRATION,
    BlockType.PROSE,
    BlockType.QUOTED_TEXT,
}


def _insert_breathing_marks(text: str) -> str:
    """Insert [breath] markers at natural pause points.

    Rules:
    - After sentence-ending period followed by space and more text
    - After semicolon or colon followed by space and more text
    - After em-dash followed by more text
    - Skip if text is too short (< 40 chars)
    """
    if len(text) < 40:
        return text

    _ABBREV = frozenset({'Prof', 'Mrs', 'Mr', 'Ms', 'Dr', 'Sr', 'Jr', 'St', 'vs'})

    def _maybe_breath(m: re.Match) -> str:
        punct, = m.groups()
        if punct in '!?':
            return f'{punct} [breath] '
        pos = m.start(1)
        if pos == 0:
            return m.group(0)
        before = m.string[:pos]
        for abbr in _ABBREV:
            la = len(abbr)
            if before.endswith(abbr) and (len(before) == la or not before[-la - 1].isalpha()):
                return m.group(0)
        if before[-1].isupper() and (len(before) == 1 or not before[-2].isalpha()):
            return m.group(0)
        return '. [breath] '

    # After ". " (sentence boundary) — but not abbreviations like "Mr. "
    text = re.sub(r'([.!?])\s+(?=[A-Z])', _maybe_breath, text)

    # After "; " or ": " (clause boundary)
    text = re.sub(r'([;:])\s+', r'\1 [breath] ', text)

    # After em-dash followed by more text
    text = re.sub(r'\u2014\s*(?=\S)', '\u2014 [breath] ', text)

    return text


def _convert_pause_notation(text: str) -> str:
    """Convert ellipses and em-dashes to visual pause markers.

    - ... (ellipsis) -> # (short pause)
    - em-dash -> ## (long pause)
    """
    # Three dots (or unicode ellipsis) -> short pause marker
    text = text.replace('\u2026', ' # ')  # unicode ellipsis
    text = re.sub(r'\.{3}', ' # ', text)

    # Em-dash -> long pause marker
    text = text.replace('\u2014', ' ## ')

    # Clean up double spaces
    text = re.sub(r'  +', ' ', text)

    return text


def _apply_post_processing(
    blocks: list[FormattedBlock],
    toggles: FormatToggles,
) -> list[FormattedBlock]:
    """Apply breathing marks and pause notation to text blocks."""
    for block in blocks:
        if block.block_type not in _TEXT_BLOCK_TYPES:
            continue
        if not block.text:
            continue

        if toggles.breathing_marks:
            block.text = _insert_breathing_marks(block.text)
        if toggles.pause_notation:
            block.text = _convert_pause_notation(block.text)

    return blocks


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# Map archetypes to their formatter functions
_ARCHETYPE_FORMATTERS = {
    Archetype.MULTI_VOICE_DRAMA: _format_multi_voice_drama,
    Archetype.SINGLE_NARRATOR: _format_single_narrator,
    Archetype.CONTINUOUS_PROSE: _format_continuous_prose,
    Archetype.DOCUMENT_ARCHIVE: _format_document_archive,
    Archetype.MIXED_MEDIA: _format_mixed_media,
}


def _attach_pronunciation_hints(
    blocks: list[FormattedBlock],
    pronunciation_guide: dict[str, str],
) -> None:
    """Attach pronunciation hints to blocks that contain flagged words."""
    for block in blocks:
        if block.block_type not in _TEXT_BLOCK_TYPES:
            continue
        if not block.text:
            continue
        for word, phonetic in pronunciation_guide.items():
            if re.search(rf"\b{re.escape(word)}\b", block.text) and f"{word} = {phonetic}" not in block.pronunciation_hints:
                block.pronunciation_hints.append(f"{word} = {phonetic}")


def _batch_by_voice(
    blocks: list[FormattedBlock],
    preflight: PreflightResult,
    color_map: dict[str, str],
    toggles: FormatToggles,
) -> list[FormattedBlock]:
    """Reorder blocks by speaker for batch recording sessions.

    Groups all lines by character, preserving section context. Each batch
    gets a header block with the character name and batch number.
    Characters are ordered by line count (most lines first).
    """
    # Collect speaker groups: {speaker_name: [blocks]}
    # Preserve insertion order by line count (most lines first)
    sorted_chars = sorted(preflight.characters, key=lambda c: c.line_count, reverse=True)
    speaker_order = [c.name for c in sorted_chars]

    # Build groups — each group collects runs of content belonging to a speaker
    # A "run" is: CHARACTER_NAME block + following DIALOGUE/STAGE_DIRECTION blocks
    speaker_groups: dict[str, list[list[FormattedBlock]]] = {name: [] for name in speaker_order}

    # Also collect narration/unattributed blocks under a special key
    narration_key = "__NARRATOR__"
    speaker_groups[narration_key] = []

    # Walk blocks and group them into speaker runs
    current_run: list[FormattedBlock] = []
    current_run_speaker: str | None = None

    # Track section context — remember the last section header seen
    last_section_header: FormattedBlock | None = None

    for block in blocks:
        bt = block.block_type

        # Skip legend/title page blocks — they go before batches
        if bt in (
            BlockType.TITLE_PAGE_TITLE, BlockType.TITLE_PAGE_INFO,
            BlockType.CHARACTER_LEGEND_HEADER, BlockType.CHARACTER_LEGEND_ENTRY,
        ):
            continue

        # Track section headers for context
        if bt == BlockType.SECTION_HEADER:
            last_section_header = block
            # Flush current run
            if current_run and current_run_speaker:
                if current_run_speaker not in speaker_groups:
                    speaker_groups[current_run_speaker] = []
                speaker_groups[current_run_speaker].append(current_run)
            elif current_run:
                speaker_groups[narration_key].append(current_run)
            current_run = []
            current_run_speaker = None
            continue

        # Section dividers and page breaks — just skip in batch mode
        if bt in (BlockType.SECTION_DIVIDER, BlockType.PAGE_BREAK):
            continue

        # Blank lines — include in current run
        if bt == BlockType.BLANK_LINE:
            if current_run:
                current_run.append(block)
            continue

        # Character name — starts a new run
        if bt == BlockType.CHARACTER_NAME:
            # Flush previous run
            if current_run and current_run_speaker:
                if current_run_speaker not in speaker_groups:
                    speaker_groups[current_run_speaker] = []
                speaker_groups[current_run_speaker].append(current_run)
            elif current_run:
                speaker_groups[narration_key].append(current_run)

            speaker = block.speaker or narration_key
            current_run = []
            current_run_speaker = speaker

            # Add section context if this is the first block for this speaker
            # in a new section
            if last_section_header:
                section_context = FormattedBlock(
                    block_type=BlockType.SECTION_HEADER,
                    text=last_section_header.text,
                    color=last_section_header.color,
                    bold=True,
                    is_centered=True,
                    font_size_override=last_section_header.font_size_override,
                )
                current_run.append(section_context)

            current_run.append(block)
            continue

        # Dialogue, stage direction, narration, etc.
        if block.speaker:
            if block.speaker != current_run_speaker:
                # Flush and switch
                if current_run and current_run_speaker:
                    if current_run_speaker not in speaker_groups:
                        speaker_groups[current_run_speaker] = []
                    speaker_groups[current_run_speaker].append(current_run)
                elif current_run:
                    speaker_groups[narration_key].append(current_run)
                current_run = []
                current_run_speaker = block.speaker
            current_run.append(block)
        else:
            # Narration or unattributed
            if current_run_speaker:
                # Still part of the current speaker's run (e.g. stage direction)
                current_run.append(block)
            else:
                current_run.append(block)
                current_run_speaker = None

    # Flush final run
    if current_run and current_run_speaker:
        if current_run_speaker not in speaker_groups:
            speaker_groups[current_run_speaker] = []
        speaker_groups[current_run_speaker].append(current_run)
    elif current_run:
        speaker_groups[narration_key].append(current_run)

    # Build batched output
    result: list[FormattedBlock] = []
    batch_num = 0

    # Include any speakers not in the preflight character list
    extra_speakers = [
        s for s in speaker_groups
        if s != narration_key and s not in speaker_order
    ]

    for speaker_name in speaker_order + extra_speakers:
        runs = speaker_groups.get(speaker_name, [])
        if not runs:
            continue

        batch_num += 1
        char_color = color_map.get(speaker_name, NARRATOR_COLOR)

        # Batch header
        result.append(FormattedBlock(
            block_type=BlockType.VOICE_BATCH_HEADER,
            text=f"BATCH {batch_num}: {speaker_name.upper()}",
            color=char_color,
            bold=True,
            is_centered=True,
            page_break_before=batch_num > 1,
        ))
        result.append(FormattedBlock(block_type=BlockType.BLANK_LINE, text=""))
        result.append(FormattedBlock(block_type=BlockType.SECTION_DIVIDER, text=""))
        result.append(FormattedBlock(block_type=BlockType.BLANK_LINE, text=""))

        # All runs for this speaker
        for run in runs:
            # Strip trailing blank lines from each run
            while run and run[-1].block_type == BlockType.BLANK_LINE:
                run.pop()
            result.extend(run)
            result.append(FormattedBlock(block_type=BlockType.BLANK_LINE, text=""))

    # Add narrator/unattributed batch if any
    narrator_runs = speaker_groups.get(narration_key, [])
    if narrator_runs:
        batch_num += 1
        result.append(FormattedBlock(
            block_type=BlockType.VOICE_BATCH_HEADER,
            text=f"BATCH {batch_num}: NARRATOR",
            color=NARRATOR_COLOR,
            bold=True,
            is_centered=True,
            page_break_before=batch_num > 1,
        ))
        result.append(FormattedBlock(block_type=BlockType.BLANK_LINE, text=""))
        result.append(FormattedBlock(block_type=BlockType.SECTION_DIVIDER, text=""))
        result.append(FormattedBlock(block_type=BlockType.BLANK_LINE, text=""))

        for run in narrator_runs:
            while run and run[-1].block_type == BlockType.BLANK_LINE:
                run.pop()
            result.extend(run)
            result.append(FormattedBlock(block_type=BlockType.BLANK_LINE, text=""))

    return result


def format_script(
    raw_text: str,
    preflight: PreflightResult,
    toggles: FormatToggles,
    filename: str,
    pronunciation_guide: dict[str, str] | None = None,
) -> list[FormattedBlock]:
    """Format a script into a list of renderable blocks.

    Args:
        raw_text: The normalized script text.
        preflight: Preflight analysis results.
        toggles: Resolved formatting toggles.
        filename: Original filename (used for title page).
        pronunciation_guide: Optional dict of word -> phonetic spelling.

    Returns:
        List of FormattedBlock ready for PDF generation.
    """
    lines = raw_text.split("\n")

    # Strip metadata blocks if toggle is on
    if toggles.strip_metadata and preflight.metadata_blocks:
        numbered_lines = _strip_metadata_blocks(lines, preflight.metadata_blocks)
    else:
        numbered_lines = [(i + 1, line) for i, line in enumerate(lines)]

    # Assign character colors
    color_map = assign_colors(preflight.characters)

    # Dispatch to archetype formatter
    formatter_fn = _ARCHETYPE_FORMATTERS.get(
        preflight.archetype, _format_multi_voice_drama
    )
    content_blocks = formatter_fn(numbered_lines, preflight, toggles, color_map)

    # Build final output
    result: list[FormattedBlock] = []

    # Title page
    if toggles.title_page:
        # Derive title from filename
        import os
        title = os.path.splitext(os.path.basename(filename))[0]
        title = title.replace("_", " ").replace("-", " ")
        result.extend(_build_title_page(title, preflight, color_map, toggles))

    # Character legend
    if toggles.character_legend and preflight.characters:
        result.extend(_build_character_legend(preflight, color_map))

    # Content
    result.extend(content_blocks)

    # Post-processing: breathing marks and pause notation
    if toggles.breathing_marks or toggles.pause_notation:
        result = _apply_post_processing(result, toggles)

    # Pronunciation hints
    if pronunciation_guide:
        _attach_pronunciation_hints(result, pronunciation_guide)

    # Cold-read line breaks
    if toggles.cold_read_breaks:
        from .cold_read import apply_cold_read_breaks
        apply_cold_read_breaks(result, toggles)

    # Voice batch mode — reorder by speaker
    if toggles.voice_batch and preflight.characters:
        result = _batch_by_voice(result, preflight, color_map, toggles)

    return result
