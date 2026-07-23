"""PDF generation using ReportLab."""

from __future__ import annotations

import logging
import os
import re as _re

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from .models import (
    BlockType,
    FormattedBlock,
    FormatToggles,
    MarginPreset,
    WRAPPABLE_INDENT_UNITS,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TrueType font registration
# ---------------------------------------------------------------------------
# Register Courier New TTF (industry standard for VO scripts) for proper
# TrueType rendering. Falls back to ReportLab's built-in Type1 Courier.

_FONT_FAMILY = "Courier"  # fallback
_FONT_BOLD = "Courier-Bold"
_FONT_ITALIC = "Courier-Oblique"
_FONT_BOLD_ITALIC = "Courier-BoldOblique"

def _font_search_dirs() -> list:
    """Font directories to scan, across OSes. Honours a VO_FONT_DIRS env override
    (os.pathsep-separated) plus the native per-OS font dirs. Courier New is used
    only if it has been installed into one of these dirs (e.g. the TTFs copied into
    ~/.local/share/fonts); the app never reads outside the local font path."""
    dirs = []
    env = os.environ.get("VO_FONT_DIRS")
    if env:
        dirs += [d for d in env.split(os.pathsep) if d]
    dirs += [
        os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/.local/share/fonts"),
        "/Library/Fonts",
        os.path.expanduser("~/Library/Fonts"),
    ]
    return dirs

# TTF families to try, in priority order. Courier New (the VO-script standard) wins
# where present; Liberation Mono is metric-compatible with Courier New and ships on most
# Linux distros, so it's the next-best TrueType before ReportLab's built-in Type-1 Courier.
_FONT_SETS = [
    {  # Courier New (Windows, or msttcorefonts on Linux/macOS)
        "CourierNew": "cour.ttf",
        "CourierNew-Bold": "courbd.ttf",
        "CourierNew-Italic": "couri.ttf",
        "CourierNew-BoldItalic": "courbi.ttf",
    },
    {  # Liberation Mono — metric-compatible Courier New substitute
        "CourierNew": "LiberationMono-Regular.ttf",
        "CourierNew-Bold": "LiberationMono-Bold.ttf",
        "CourierNew-Italic": "LiberationMono-Italic.ttf",
        "CourierNew-BoldItalic": "LiberationMono-BoldItalic.ttf",
    },
]


def _index_fonts(filenames: set) -> dict:
    """One pass over the search dirs, mapping each wanted basename to a full path."""
    found: dict = {}
    for d in _font_search_dirs():
        if not os.path.isdir(d):
            continue
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if fn in filenames and fn not in found:
                    found[fn] = os.path.join(root, fn)
            if len(found) == len(filenames):
                return found
    return found


def _register_fonts() -> None:
    """Register a Courier New TTF family if available, else a metric-compatible
    substitute (Liberation Mono); otherwise keep ReportLab's Type-1 Courier."""
    global _FONT_FAMILY, _FONT_BOLD, _FONT_ITALIC, _FONT_BOLD_ITALIC

    wanted = {fn for fs in _FONT_SETS for fn in fs.values()}
    found = _index_fonts(wanted)

    for font_set in _FONT_SETS:
        resolved = {name: found.get(fn) for name, fn in font_set.items()}
        if not all(resolved.values()):
            continue
        try:
            for name, path in resolved.items():
                pdfmetrics.registerFont(TTFont(name, path))
            pdfmetrics.registerFontFamily(
                "CourierNew",
                normal="CourierNew",
                bold="CourierNew-Bold",
                italic="CourierNew-Italic",
                boldItalic="CourierNew-BoldItalic",
            )
            _FONT_FAMILY = "CourierNew"
            _FONT_BOLD = "CourierNew-Bold"
            _FONT_ITALIC = "CourierNew-Italic"
            _FONT_BOLD_ITALIC = "CourierNew-BoldItalic"
            log.info("Registered monospace family from %s", resolved["CourierNew"])
            return
        except Exception as e:
            log.warning(
                "Failed to register TTF family (%s); trying next candidate.", e,
            )

    log.info(
        "No Courier New / Liberation Mono TTFs found; falling back to Type-1 Courier."
    )

_register_fonts()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARGIN_PRESETS = {
    MarginPreset.NORMAL: {
        "leftMargin": 1 * inch,
        "rightMargin": 1 * inch,
        "topMargin": 1 * inch,
        "bottomMargin": 1 * inch,
    },
    MarginPreset.WIDE: {
        "leftMargin": 1.5 * inch,
        "rightMargin": 1.5 * inch,
        "topMargin": 1 * inch,
        "bottomMargin": 1 * inch,
    },
    MarginPreset.EXTRA: {
        "leftMargin": 2 * inch,
        "rightMargin": 2 * inch,
        "topMargin": 1 * inch,
        "bottomMargin": 1 * inch,
    },
    MarginPreset.NARROW: {
        "leftMargin": 0.8 * inch,
        "rightMargin": 0.8 * inch,
        "topMargin": 0.6 * inch,
        "bottomMargin": 0.6 * inch,
    },
}

INDENT_SIZE = 0.5 * inch


# ---------------------------------------------------------------------------
# Custom flowables
# ---------------------------------------------------------------------------


class HorizontalRule(Flowable):
    """Draws a horizontal rule across the page width."""

    def __init__(self, width: float, thickness: float = 0.5, color: str = "#CCCCCC"):
        super().__init__()
        self.rule_width = width
        self.thickness = thickness
        self.rule_color = HexColor(color)

    def wrap(self, availWidth, availHeight):
        self.rule_width = availWidth
        return (availWidth, self.thickness + 6)

    def draw(self):
        self.canv.setStrokeColor(self.rule_color)
        self.canv.setLineWidth(self.thickness)
        y = 3
        self.canv.line(0, y, self.rule_width, y)


class ColorSwatch(Flowable):
    """Draws a small colored square for the character legend."""

    def __init__(self, color: str, size: float = 10):
        super().__init__()
        self.swatch_color = HexColor(color)
        self.size = size

    def wrap(self, availWidth, availHeight):
        return (self.size, self.size)

    def draw(self):
        self.canv.setFillColor(self.swatch_color)
        self.canv.rect(0, 0, self.size, self.size, fill=1, stroke=0)


# ---------------------------------------------------------------------------
# XML escaping for ReportLab Paragraph markup
# ---------------------------------------------------------------------------


def _xml_escape(text: str) -> str:
    """Escape text for use in ReportLab's XML-based Paragraph markup."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


# Bold: **text** -> <b>text</b>  (must be matched before italic)
_RE_MD_BOLD = _re.compile(r"\*\*(.+?)\*\*")
# Italic: *text* -> <i>text</i>  (not preceded/followed by another *)
_RE_MD_ITALIC = _re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")


def _md_to_markup(escaped_text: str) -> str:
    """Convert preserved markdown bold/italic to ReportLab XML tags.

    Must be called *after* _xml_escape so that angle brackets in the
    original text are already escaped and won't collide with the tags
    we insert here.
    """
    # Bold first (** before *)
    text = _RE_MD_BOLD.sub(r"<b>\1</b>", escaped_text)
    # Then italic
    text = _RE_MD_ITALIC.sub(r"<i>\1</i>", text)
    return text


# ---------------------------------------------------------------------------
# Style building
# ---------------------------------------------------------------------------


def _build_styles(toggles: FormatToggles) -> dict[str, ParagraphStyle]:
    """Build paragraph styles for each block type."""
    base_size = toggles.font_size
    leading = base_size * toggles.line_spacing

    styles = {}

    styles["base"] = ParagraphStyle(
        name="base",
        fontName=_FONT_FAMILY,
        fontSize=base_size,
        leading=leading,
        alignment=TA_LEFT,
        spaceAfter=0,
        spaceBefore=0,
    )

    styles["character_name"] = ParagraphStyle(
        name="character_name",
        parent=styles["base"],
        fontName=_FONT_BOLD,
        spaceAfter=0,
        spaceBefore=leading * 0.3,
    )

    styles["dialogue"] = ParagraphStyle(
        name="dialogue",
        parent=styles["base"],
        leftIndent=WRAPPABLE_INDENT_UNITS[BlockType.DIALOGUE] * INDENT_SIZE,
    )

    styles["stage_direction"] = ParagraphStyle(
        name="stage_direction",
        parent=styles["base"],
        fontName=_FONT_ITALIC,
        fontSize=base_size - 2,
        leading=(base_size - 2) * toggles.line_spacing,
        leftIndent=INDENT_SIZE,
    )

    styles["sound_cue"] = ParagraphStyle(
        name="sound_cue",
        parent=styles["base"],
        fontSize=base_size - 2,
        leading=(base_size - 2) * toggles.line_spacing,
        alignment=TA_CENTER,
        spaceBefore=leading * 0.2,
        spaceAfter=leading * 0.2,
    )

    styles["section_header"] = ParagraphStyle(
        name="section_header",
        parent=styles["base"],
        fontName=_FONT_BOLD,
        fontSize=base_size + 2,
        leading=(base_size + 2) * toggles.line_spacing,
        alignment=TA_CENTER,
        spaceBefore=leading * 0.5,
        spaceAfter=leading * 0.5,
    )

    styles["narration"] = ParagraphStyle(
        name="narration",
        parent=styles["base"],
        leftIndent=WRAPPABLE_INDENT_UNITS[BlockType.NARRATION] * INDENT_SIZE,
    )

    styles["prose"] = ParagraphStyle(
        name="prose",
        parent=styles["base"],
        leftIndent=WRAPPABLE_INDENT_UNITS[BlockType.PROSE] * INDENT_SIZE,
        spaceAfter=leading * 0.3,
    )

    styles["quoted_text"] = ParagraphStyle(
        name="quoted_text",
        parent=styles["base"],
        leftIndent=WRAPPABLE_INDENT_UNITS[BlockType.QUOTED_TEXT] * INDENT_SIZE,
    )

    styles["title"] = ParagraphStyle(
        name="title",
        parent=styles["base"],
        fontName=_FONT_BOLD,
        fontSize=base_size + 6,
        leading=(base_size + 6) * 1.5,
        alignment=TA_CENTER,
        spaceBefore=leading,
        spaceAfter=leading,
    )

    styles["title_info"] = ParagraphStyle(
        name="title_info",
        parent=styles["base"],
        leftIndent=INDENT_SIZE,
    )

    styles["legend_header"] = ParagraphStyle(
        name="legend_header",
        parent=styles["base"],
        fontName=_FONT_BOLD,
        alignment=TA_CENTER,
        spaceBefore=leading * 0.5,
        spaceAfter=leading * 0.3,
    )

    styles["legend_entry"] = ParagraphStyle(
        name="legend_entry",
        parent=styles["base"],
        fontName=_FONT_BOLD,
        leftIndent=INDENT_SIZE,
    )

    styles["batch_header"] = ParagraphStyle(
        name="batch_header",
        parent=styles["base"],
        fontName=_FONT_BOLD,
        fontSize=base_size + 4,
        leading=(base_size + 4) * 1.5,
        alignment=TA_CENTER,
        spaceBefore=leading * 0.5,
        spaceAfter=leading * 0.5,
    )

    styles["intro"] = ParagraphStyle(
        name="intro",
        parent=styles["base"],
        fontName=_FONT_ITALIC,
        leftIndent=INDENT_SIZE,
        rightIndent=INDENT_SIZE,
        spaceBefore=leading * 0.3,
        spaceAfter=leading * 0.5,
    )

    styles["outro"] = ParagraphStyle(
        name="outro",
        parent=styles["base"],
        fontName=_FONT_ITALIC,
        leftIndent=INDENT_SIZE,
        rightIndent=INDENT_SIZE,
        spaceBefore=leading * 0.5,
        spaceAfter=leading * 0.3,
    )

    styles["source_label"] = ParagraphStyle(
        name="source_label",
        parent=styles["base"],
        fontName=_FONT_BOLD,
        fontSize=base_size - 1,
        leading=(base_size - 1) * toggles.line_spacing,
        alignment=TA_CENTER,
        spaceBefore=4,
        spaceAfter=4,
    )

    annotation_size = max(base_size - 4, 10)
    styles["pronunciation_annotation"] = ParagraphStyle(
        name="pronunciation_annotation",
        parent=styles["base"],
        fontName=_FONT_ITALIC,
        fontSize=annotation_size,
        leading=annotation_size * 1.4,
        leftIndent=INDENT_SIZE,
        textColor=HexColor("#6B7280"),
        spaceBefore=0,
        spaceAfter=2,
    )

    return styles


# ---------------------------------------------------------------------------
# Block to flowable conversion
# ---------------------------------------------------------------------------

# Map block types to style names
_BLOCK_STYLE_MAP = {
    BlockType.CHARACTER_NAME: "character_name",
    BlockType.DIALOGUE: "dialogue",
    BlockType.STAGE_DIRECTION: "stage_direction",
    BlockType.SOUND_CUE: "sound_cue",
    BlockType.SECTION_HEADER: "section_header",
    BlockType.NARRATION: "narration",
    BlockType.PROSE: "prose",
    BlockType.QUOTED_TEXT: "quoted_text",
    BlockType.TITLE_PAGE_TITLE: "title",
    BlockType.TITLE_PAGE_INFO: "title_info",
    BlockType.CHARACTER_LEGEND_HEADER: "legend_header",
    BlockType.CHARACTER_LEGEND_ENTRY: "legend_entry",
    BlockType.VOICE_BATCH_HEADER: "batch_header",
    BlockType.SOURCE_LABEL_OPEN: "source_label",
    BlockType.SOURCE_LABEL_CLOSE: "base",
    BlockType.INTRO: "intro",
    BlockType.OUTRO: "outro",
}


def _block_to_flowables(
    block: FormattedBlock,
    styles: dict[str, ParagraphStyle],
    toggles: FormatToggles,
) -> list[Flowable]:
    """Convert a FormattedBlock to ReportLab flowables."""
    # Page break
    if block.block_type == BlockType.PAGE_BREAK:
        return [PageBreak()]

    # Page break before this block
    result: list[Flowable] = []
    if block.page_break_before:
        result.append(PageBreak())

    # Blank line
    if block.block_type == BlockType.BLANK_LINE:
        base_leading = toggles.font_size * toggles.line_spacing
        result.append(Spacer(1, base_leading * 0.5))
        return result

    # Section divider (horizontal rule)
    if block.block_type == BlockType.SECTION_DIVIDER:
        result.append(Spacer(1, 6))
        result.append(HorizontalRule(0))  # width gets set in wrap()
        result.append(Spacer(1, 6))
        return result

    # Source label open: rule + bold label + rule
    if block.block_type == BlockType.SOURCE_LABEL_OPEN:
        result.append(Spacer(1, 8))
        result.append(HorizontalRule(0, thickness=0.75, color="#999999"))
        if block.text:
            style = styles.get("source_label", styles["base"])
            markup = f"<b>{_xml_escape(block.text)}</b>"
            result.append(Paragraph(markup, style))
        result.append(HorizontalRule(0, thickness=0.75, color="#999999"))
        result.append(Spacer(1, 4))
        return result

    # Source label close: rule + spacer
    if block.block_type == BlockType.SOURCE_LABEL_CLOSE:
        result.append(Spacer(1, 4))
        result.append(HorizontalRule(0, thickness=0.5, color="#CCCCCC"))
        result.append(Spacer(1, 8))
        return result

    # Text blocks
    style_name = _BLOCK_STYLE_MAP.get(block.block_type, "base")
    base_style = styles.get(style_name, styles["base"])

    # Create a derived style if we need to override anything
    overrides: dict = {}

    if block.font_size_override:
        overrides["fontSize"] = block.font_size_override
        overrides["leading"] = block.font_size_override * toggles.line_spacing

    if block.indent_level > 0 and block.block_type not in (
        BlockType.DIALOGUE,
        BlockType.STAGE_DIRECTION,
        BlockType.NARRATION,
        BlockType.QUOTED_TEXT,
        BlockType.TITLE_PAGE_INFO,
        BlockType.CHARACTER_LEGEND_ENTRY,
    ):
        overrides["leftIndent"] = block.indent_level * INDENT_SIZE

    if block.is_centered:
        overrides["alignment"] = TA_CENTER

    if overrides:
        style = ParagraphStyle(
            name=f"{style_name}_override",
            parent=base_style,
            **overrides,
        )
    else:
        style = base_style

    # Build the paragraph markup
    escaped_text = _xml_escape(block.text)

    # Convert inline markdown emphasis to ReportLab tags
    escaped_text = _md_to_markup(escaped_text)

    # Apply formatting tags
    markup = escaped_text
    if block.color and block.color != "#000000":
        markup = f'<font color="{block.color}">{markup}</font>'
    if block.bold:
        markup = f"<b>{markup}</b>"
    if block.italic:
        markup = f"<i>{markup}</i>"

    # Convert cold-read line breaks to ReportLab forced breaks
    markup = markup.replace("\n", "<br/>")

    if not markup.strip():
        # Don't create empty paragraphs
        return result

    para = Paragraph(markup, style)
    result.append(para)

    # Pronunciation annotations
    if block.pronunciation_hints:
        annot_style = styles.get("pronunciation_annotation", styles["base"])
        for hint in block.pronunciation_hints:
            hint_markup = f"<i>[{_xml_escape(hint)}]</i>"
            result.append(Paragraph(hint_markup, annot_style))

    return result


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------


def generate_pdf(
    blocks: list[FormattedBlock],
    output_path: str,
    toggles: FormatToggles,
) -> str:
    """Generate a PDF from formatted blocks.

    Args:
        blocks: List of FormattedBlock from the formatter.
        output_path: Path to write the PDF file.
        toggles: Formatting toggles.

    Returns:
        The output file path.
    """
    margins = MARGIN_PRESETS.get(toggles.margins, MARGIN_PRESETS[MarginPreset.WIDE])

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        **margins,
    )

    styles = _build_styles(toggles)
    flowables: list[Flowable] = []

    # Process blocks, handling KeepTogether for character_name + next block
    i = 0
    while i < len(blocks):
        block = blocks[i]
        block_flowables = _block_to_flowables(block, styles, toggles)

        if block.keep_with_next and i + 1 < len(blocks):
            # Group this block with the next one to prevent page break between them
            next_block = blocks[i + 1]
            next_flowables = _block_to_flowables(next_block, styles, toggles)
            # Only use KeepTogether if neither is a page break
            if (
                block.block_type != BlockType.PAGE_BREAK
                and next_block.block_type != BlockType.PAGE_BREAK
            ):
                flowables.append(KeepTogether(block_flowables + next_flowables))
                i += 2
                continue

        flowables.extend(block_flowables)
        i += 1

    doc.build(flowables)
    return output_path
