"""Shared data structures and enums for ColdRead."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Archetype(str, Enum):
    DOCUMENT_ARCHIVE = "document_archive"
    MULTI_VOICE_DRAMA = "multi_voice_drama"
    SINGLE_NARRATOR = "single_narrator"
    CONTINUOUS_PROSE = "continuous_prose"
    MIXED_MEDIA = "mixed_media"


class NarratorStyle(str, Enum):
    NORMAL = "normal"
    ITALIC = "italic"
    BOLD = "bold"


class QuotedTextStyle(str, Enum):
    INDENT = "indent"
    ITALIC = "italic"
    INDENT_ITALIC = "indent+italic"
    NONE = "none"


class MarginPreset(str, Enum):
    NORMAL = "normal"
    WIDE = "wide"
    EXTRA = "extra"
    NARROW = "narrow"


# Canonical leftIndent for the block types that go through cold-read line
# wrapping. Units are multiples of 0.5" (one "indent step"). pdf_writer.py
# uses this to set ParagraphStyle.leftIndent; cold_read.py uses it to compute
# max line width. Keep them in lockstep by not hardcoding either value.
WRAPPABLE_INDENT_UNITS: dict["BlockType", int] = {}  # populated below


class BlockType(str, Enum):
    TITLE_PAGE_TITLE = "title_page_title"
    TITLE_PAGE_INFO = "title_page_info"
    CHARACTER_LEGEND_HEADER = "character_legend_header"
    CHARACTER_LEGEND_ENTRY = "character_legend_entry"
    CHARACTER_NAME = "character_name"
    DIALOGUE = "dialogue"
    STAGE_DIRECTION = "stage_direction"
    SOUND_CUE = "sound_cue"
    SECTION_HEADER = "section_header"
    SECTION_DIVIDER = "section_divider"
    SOURCE_LABEL_OPEN = "source_label_open"
    SOURCE_LABEL_CLOSE = "source_label_close"
    NARRATION = "narration"
    PROSE = "prose"
    QUOTED_TEXT = "quoted_text"
    BLANK_LINE = "blank_line"
    PAGE_BREAK = "page_break"
    VOICE_BATCH_HEADER = "voice_batch_header"
    INTRO = "intro"
    OUTRO = "outro"


WRAPPABLE_INDENT_UNITS.update({
    BlockType.DIALOGUE:    1,
    BlockType.NARRATION:   1,
    BlockType.QUOTED_TEXT: 2,
    BlockType.PROSE:       0,
})


class LineType(str, Enum):
    CHARACTER_NAME = "character_name"
    DIALOGUE = "dialogue"
    STAGE_DIRECTION = "stage_direction"
    SOUND_CUE = "sound_cue"
    SECTION_HEADER = "section_header"
    SOURCE_LABEL = "source_label"
    NARRATION = "narration"
    PROSE = "prose"
    METADATA = "metadata"
    BLANK = "blank"
    HORIZONTAL_RULE = "horizontal_rule"
    QUOTED_TEXT = "quoted_text"


# ---------------------------------------------------------------------------
# Preflight data structures
# ---------------------------------------------------------------------------

@dataclass
class CharacterInfo:
    name: str
    line_count: int
    suggested_color: str  # hex color e.g. "#2563EB"


@dataclass
class SourceType:
    type: str  # broadcast, email, transmission, document_ref, etc.
    label: Optional[str] = None
    prefix: Optional[str] = None
    count: int = 0


@dataclass
class Section:
    title: str
    start_line: int
    end_line: int


@dataclass
class MetadataBlock:
    type: str  # youtube_title, runtime_estimate, voice_cast, editorial_note, etc.
    start_line: int
    end_line: int
    text: Optional[str] = None


@dataclass
class PronunciationFlag:
    word: str
    line: int


@dataclass
class PreflightResult:
    archetype: Archetype
    characters: list[CharacterInfo]
    has_narrator: bool
    source_types: list[SourceType]
    sections: list[Section]
    detected_stage_directions: bool
    detected_sound_cues: bool
    metadata_blocks: list[MetadataBlock]
    pronunciation_flags: list[PronunciationFlag]
    suggested_toggles: dict[str, Any]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Toggle configuration
# ---------------------------------------------------------------------------

@dataclass
class FormatToggles:
    color_characters: bool = True
    narrator_style: NarratorStyle = NarratorStyle.NORMAL
    section_breaks: bool = True
    stage_directions: bool = True
    sound_cues: bool = True
    source_labels: bool = False
    quoted_text_style: QuotedTextStyle = QuotedTextStyle.INDENT
    strip_metadata: bool = True
    title_page: bool = False
    character_legend: bool = True
    breathing_marks: bool = False
    pause_notation: bool = False
    pronunciation_guide: bool = False
    voice_batch: bool = False
    cold_read_breaks: bool = False
    font_size: int = 16
    line_spacing: float = 2.0
    margins: MarginPreset = MarginPreset.WIDE


# ---------------------------------------------------------------------------
# Formatter output
# ---------------------------------------------------------------------------

@dataclass
class FormattedBlock:
    """A single renderable block for the PDF writer."""
    block_type: BlockType
    text: str
    color: str = "#000000"
    bold: bool = False
    italic: bool = False
    indent_level: int = 0  # 0 = flush left, 1 = one tab, 2 = two tabs
    font_size_override: Optional[int] = None
    keep_with_next: bool = False
    page_break_before: bool = False
    is_centered: bool = False
    source_line: Optional[int] = None  # line number in original script
    match_pattern: Optional[str] = None  # regex pattern that matched (for diagnostics)
    pronunciation_hints: list[str] = field(default_factory=list)  # phonetic hints for this block
    speaker: Optional[str] = None  # character name for dialogue/stage direction blocks
    source_type: Optional[str] = None  # "broadcast", "email", "transmission", "document_ref", etc.


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

@dataclass
class DiagnosticEntry:
    line_number: int
    original_text: str
    assigned_type: str
    issue: str
    suggestion: str


@dataclass
class DiagnosticReport:
    misclassified_lines: list[DiagnosticEntry]
    missed_characters: list[str]
    missed_stage_directions: list[int]
    missed_sound_cues: list[int]
    unstripped_metadata: list[int]
    unhandled_patterns: list[str]
    summary: str
