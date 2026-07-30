"""Read a finished ColdRead PDF back into styled lines.

ColdRead PDFs are born-digital: ReportLab embeds a real text layer, so color,
weight, slant, size and horizontal position are all exactly recoverable. This
module recovers them and expresses position and size *relatively*, so the result
does not depend on which margin preset or font size produced the PDF.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import fitz

# Courier's advance width as a fraction of the em. Used to convert a horizontal
# offset in points into a count of character widths.
COURIER_ADVANCE_RATIO = 0.6

# A vertical gap this much larger than the document's normal leading is treated
# as a blank line rather than ordinary line spacing.
PARAGRAPH_GAP_FACTOR = 1.5

# PyMuPDF span flag bits.
_FLAG_ITALIC = 1 << 1
_FLAG_BOLD = 1 << 4


class ReadViewError(Exception):
    """Raised when a PDF cannot be turned into a read-view."""


@dataclass(frozen=True)
class ReadLine:
    text: str
    color: str
    bold: bool
    italic: bool
    size_ratio: float
    indent: int
    gap_before: bool


@dataclass(frozen=True)
class ReadScript:
    title: str
    lines: list[ReadLine]
    word_count: int
    page_count: int
    derived: str

    @property
    def words_per_line(self) -> float:
        if not self.lines:
            return 0.0
        return self.word_count / len(self.lines)


@dataclass(frozen=True)
class _RawLine:
    page: int
    y0: float
    x0: float
    text: str
    color: str
    bold: bool
    italic: bool
    size: float


def _harvest(doc: fitz.Document) -> list[_RawLine]:
    raw: list[_RawLine] = []
    for page_no, page in enumerate(doc):
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:  # 0 = text; 1 = image
                continue
            for line in block["lines"]:
                text = "".join(span["text"] for span in line["spans"]).rstrip()
                if not text.strip():
                    continue
                span = line["spans"][0]
                font = span["font"]
                flags = span["flags"]
                raw.append(
                    _RawLine(
                        page=page_no,
                        y0=round(line["bbox"][1], 1),
                        x0=line["bbox"][0],
                        text=text,
                        color=f"#{span['color']:06x}",
                        bold="Bold" in font or bool(flags & _FLAG_BOLD),
                        italic="Italic" in font
                        or "Oblique" in font
                        or bool(flags & _FLAG_ITALIC),
                        size=round(span["size"], 1),
                    )
                )
    # ReportLab emits in reading order, but sorting is cheap insurance against a
    # producer that does not.
    raw.sort(key=lambda r: (r.page, r.y0, r.x0))
    return raw


def _modal_size(raw: list[_RawLine]) -> float:
    """The body text size: the size covering the most characters."""
    weighted: Counter[float] = Counter()
    for line in raw:
        weighted[line.size] += len(line.text)
    return weighted.most_common(1)[0][0]


def _normal_leading(raw: list[_RawLine]) -> float:
    """The document's ordinary line-to-line spacing."""
    gaps: Counter[float] = Counter()
    for prev, curr in zip(raw, raw[1:]):
        if curr.page != prev.page:
            continue
        gap = round(curr.y0 - prev.y0, 1)
        if gap > 0:
            gaps[gap] += 1
    if not gaps:
        return 0.0
    return gaps.most_common(1)[0][0]


def extract_lines(pdf_path: str | Path) -> ReadScript:
    """Read `pdf_path` into a ReadScript.

    Raises ReadViewError if the file cannot be opened or yields no text — an
    empty read-view is indistinguishable from a successful one, so this must
    never return quietly.
    """
    path = Path(pdf_path)
    try:
        doc = fitz.open(str(path))
    except Exception as exc:  # noqa: BLE001 - surface any reader failure alike
        raise ReadViewError(f"cannot open {path.name}: {exc}") from exc

    try:
        raw = _harvest(doc)
        page_count = doc.page_count
    finally:
        doc.close()

    if not raw:
        raise ReadViewError(
            f"{path.name}: no extractable text layer "
            "(scanned, encrypted, or image-only?)"
        )

    body_size = _modal_size(raw)
    min_x0 = min(line.x0 for line in raw)
    leading = _normal_leading(raw)
    gap_threshold = leading * PARAGRAPH_GAP_FACTOR if leading else float("inf")
    char_width = body_size * COURIER_ADVANCE_RATIO

    lines: list[ReadLine] = []
    for index, line in enumerate(raw):
        if index == 0:
            gap_before = False
        else:
            prev = raw[index - 1]
            # A page boundary is not a paragraph break — removing page seams is
            # the entire point of this view.
            gap_before = (
                prev.page == line.page and (line.y0 - prev.y0) > gap_threshold
            )
        lines.append(
            ReadLine(
                text=line.text,
                color=line.color,
                bold=line.bold,
                italic=line.italic,
                size_ratio=round(line.size / body_size, 3),
                indent=round((line.x0 - min_x0) / char_width) if char_width else 0,
                gap_before=gap_before,
            )
        )

    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return ReadScript(
        title=path.stem,
        lines=lines,
        word_count=sum(len(line.text.split()) for line in lines),
        page_count=page_count,
        derived=mtime.isoformat(timespec="seconds"),
    )
