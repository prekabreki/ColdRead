# PDF Teleprompter Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn an already-rendered ColdRead PDF into one self-contained auto-scrolling teleprompter HTML file that opens on an iPad from OneDrive with no server.

**Architecture:** A new, entirely additive `vo_format/readview/` package. `extract.py` reads a PDF's born-digital text layer with PyMuPDF into `ReadLine` dataclasses carrying text, color, weight, slant, relative size, relative indent and paragraph-gap flags. `theme.py` maps ColdRead's print palette to a dark-legible twin at the same hue. `render.py` assembles those into one HTML document with all CSS and JS inlined. `cli.py` exposes it as `coldread-readview`.

**Tech Stack:** Python 3.10+, PyMuPDF (`fitz`, already a dependency), pytest. Client side is vanilla JS and CSS — no framework, no build step, no external asset of any kind.

**Design spec:** `docs/superpowers/specs/2026-07-30-pdf-teleprompter-design.md`. Read it before starting. Phase 2 (the Raspberry Pi server, token auth, the library index) is **out of scope for this plan**.

## Global Constraints

Every task's requirements implicitly include all of these.

- **Python `>=3.10`** (from `pyproject.toml` `requires-python`). No `match` statements gated on 3.11+, no `typing.Self`.
- **Zero new runtime dependencies.** `pymupdf>=1.24.0,<2` is already declared and is the only third-party import permitted in this package. Adding anything to `[project.dependencies]` is a plan violation.
- **No existing Python module may be modified.** `cli.py`, `formatter.py`, `pdf_writer.py`, `models.py`, `colors.py`, `cold_read.py`, `parser.py`, `gui.py` are all off limits. Only new files under `vo_format/readview/`, new files under `tests/`, plus `pyproject.toml` and `README.md`.
- **Rendered HTML must be fully self-contained.** No `<script src>`, no `<link rel=stylesheet>`, no `@import`, no external font, no `http://` or `https://` URL anywhere in the output. It must render correctly with the network off.
- **No script text may pass through any API.** This operates on finished PDFs only.
- **Every file starts with `from __future__ import annotations`**, matching the rest of the codebase.
- **Lint before every commit:** `./.venv/bin/ruff check vo_format/readview tests/` must pass. Config is `line-length = 88`, `lint.select = ["E", "F", "I"]` — note `I` means imports must be sorted.
- **Tests require no API key.** Nothing in this plan calls Anthropic.
- **No committed binary fixtures.** Test PDFs are generated in-test. A committed fixture can be silently excluded by a `.gitignore` rule while local runs stay green forever.
- **Run the full suite before every commit:** `./.venv/bin/python -m pytest tests/ -q`. It must stay green — this plan must not break the 10 existing test modules.

## File Structure

| File | Responsibility |
| --- | --- |
| `vo_format/readview/__init__.py` | Package marker; re-exports `extract_lines`, `render`, `ReadViewError` |
| `vo_format/readview/extract.py` | PDF → `ReadScript`. The only file that imports `fitz`. |
| `vo_format/readview/theme.py` | Print-palette → dark-palette mapping, plus luminance/contrast helpers |
| `vo_format/readview/assets.py` | The keep-awake video as a base64 constant |
| `vo_format/readview/reader.css` | All page styling |
| `vo_format/readview/reader.js` | All reading behavior: scroll loop, touch, keyboard, keep-awake, persistence |
| `vo_format/readview/render.py` | `ReadScript` → one self-contained HTML string; inlines the two assets |
| `vo_format/readview/cli.py` | `coldread-readview` entry point: globs, idempotence, canary output |
| `tests/conftest.py` | Shared fixture that generates a real PDF from a bundled sample |
| `tests/test_readview_extract.py` | Round-trip and fidelity tests for extraction |
| `tests/test_readview_theme.py` | WCAG AA and grayscale-distinctness tests for the dark palette |
| `tests/test_readview_render.py` | Structure and self-containment tests |
| `tests/test_readview_cli.py` | Idempotence, canary, and hard-failure tests |
| `pyproject.toml` | Add the entry point and the package-data globs |
| `README.md` | Document the read-view |

---

### Task 1: Extraction — PDF to ReadScript

**Files:**
- Create: `vo_format/readview/__init__.py`
- Create: `vo_format/readview/extract.py`
- Create: `tests/conftest.py`
- Test: `tests/test_readview_extract.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. Uses `fitz` (PyMuPDF), and in tests `vo_format.formatter.format_script`, `vo_format.pdf_writer.generate_pdf`, `vo_format.toggles.resolve_toggles`.
- Produces:
  - `ReadViewError(Exception)`
  - `ReadLine` frozen dataclass: `text: str`, `color: str`, `bold: bool`, `italic: bool`, `size_ratio: float`, `indent: int`, `gap_before: bool`
  - `ReadScript` frozen dataclass: `title: str`, `lines: list[ReadLine]`, `word_count: int`, `page_count: int`, `derived: str`; property `words_per_line -> float`
  - `extract_lines(pdf_path: str | Path) -> ReadScript`
  - `COURIER_ADVANCE_RATIO: float = 0.6`

- [ ] **Step 1: Write the conftest fixture that generates a real PDF**

Create `tests/conftest.py`:

```python
"""Shared fixtures for the readview tests.

The readview package consumes finished PDFs, so its tests need real ones.
They are generated here rather than committed: a committed binary fixture can
be silently excluded by a .gitignore rule while local runs stay green.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vo_format.formatter import format_script
from vo_format.models import (
    Archetype,
    FormattedBlock,
    FormatToggles,
    PreflightResult,
)
from vo_format.parser import extract_text, normalize_text
from vo_format.pdf_writer import generate_pdf
from vo_format.toggles import resolve_toggles

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "vo_format" / "samples"

ARCHETYPE_SAMPLE_MAP: dict[Archetype, str] = {
    Archetype.DOCUMENT_ARCHIVE: "document_archive_sample.md",
    Archetype.MULTI_VOICE_DRAMA: "multi_voice_drama_sample.md",
    Archetype.SINGLE_NARRATOR: "single_narrator_sample.md",
    Archetype.CONTINUOUS_PROSE: "continuous_prose_sample.md",
    Archetype.MIXED_MEDIA: "mixed_media_sample.md",
}


def _empty_preflight(archetype: Archetype) -> PreflightResult:
    return PreflightResult(
        archetype=archetype,
        characters=[],
        has_narrator=True,
        source_types=[],
        sections=[],
        detected_stage_directions=False,
        detected_sound_cues=False,
        metadata_blocks=[],
        pronunciation_flags=[],
        suggested_toggles={},
        warnings=[],
    )


@pytest.fixture
def sample_pdf(tmp_path: Path):
    """Render a bundled sample to a real PDF.

    Returns a callable: (archetype) -> (pdf_path, blocks) so a test can compare
    what went in against what comes back out.
    """

    def _make(archetype: Archetype) -> tuple[Path, list[FormattedBlock]]:
        sample_name = ARCHETYPE_SAMPLE_MAP[archetype]
        text = normalize_text(extract_text(str(SAMPLE_DIR / sample_name))[0])
        toggles = resolve_toggles(archetype=archetype)
        blocks = format_script(
            raw_text=text,
            preflight=_empty_preflight(archetype),
            toggles=toggles,
            filename=sample_name,
        )
        out = tmp_path / f"{archetype.value}.pdf"
        generate_pdf(blocks=blocks, output_path=str(out), toggles=toggles)
        return out, blocks

    return _make


@pytest.fixture
def blocks_pdf(tmp_path: Path):
    """Render an explicit list of FormattedBlocks to a real PDF.

    Used where a test needs to control colors/indents/gaps exactly rather than
    depend on whatever a sample happens to contain.
    """

    def _make(blocks: list[FormattedBlock], name: str = "blocks.pdf") -> Path:
        out = tmp_path / name
        generate_pdf(blocks=blocks, output_path=str(out), toggles=FormatToggles())
        return out

    return _make
```

- [ ] **Step 2: Write the failing extraction tests**

Create `tests/test_readview_extract.py`:

```python
"""Fidelity tests for readview extraction.

The claim under test is that ColdRead's PDFs are born-digital, so everything the
formatter put in is exactly recoverable. Each test pins one recovered property.
"""

from __future__ import annotations

import re

import pytest

from vo_format.models import Archetype, BlockType, FormattedBlock
from vo_format.readview.extract import ReadViewError, extract_lines

NON_TEXT_BLOCKS = frozenset(
    {BlockType.BLANK_LINE, BlockType.PAGE_BREAK, BlockType.SECTION_DIVIDER}
)


def _fold(s: str) -> str:
    """Collapse whitespace and drop emphasis markers.

    pdf_writer converts **bold** to real bold, so the asterisks are absent from
    the PDF text layer while present in the block. Comparing without folding
    yields false negatives.
    """
    return re.sub(r"\s+", " ", s.replace("*", "").replace("_", "")).strip()


class TestRoundTrip:
    @pytest.mark.parametrize("archetype", list(Archetype))
    def test_every_block_text_survives_the_pdf(self, archetype, sample_pdf) -> None:
        pdf_path, blocks = sample_pdf(archetype)

        script = extract_lines(pdf_path)
        haystack = _fold(" ".join(line.text for line in script.lines))

        missing = [
            b.text
            for b in blocks
            if b.block_type not in NON_TEXT_BLOCKS
            and _fold(b.text)
            and _fold(b.text) not in haystack
        ]
        assert not missing, f"{len(missing)} block(s) lost, first: {missing[:3]!r}"

    def test_line_and_word_counts_are_populated(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        script = extract_lines(pdf_path)
        assert len(script.lines) > 10
        assert script.word_count > len(script.lines)
        assert script.page_count >= 1
        assert script.words_per_line > 1.0

    def test_title_is_the_pdf_stem(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        assert extract_lines(pdf_path).title == pdf_path.stem

    def test_derived_is_iso_8601(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        from datetime import datetime

        datetime.fromisoformat(extract_lines(pdf_path).derived)


class TestStyleFidelity:
    def test_colors_round_trip(self, blocks_pdf) -> None:
        blocks = [
            FormattedBlock(BlockType.DIALOGUE, "Zebra line one", color="#2563EB"),
            FormattedBlock(BlockType.DIALOGUE, "Zebra line two", color="#DC2626"),
            FormattedBlock(BlockType.DIALOGUE, "Zebra line three", color="#16A34A"),
        ]
        script = extract_lines(blocks_pdf(blocks))
        found = {
            line.color.lower()
            for line in script.lines
            if line.text.startswith("Zebra")
        }
        assert {"#2563eb", "#dc2626", "#16a34a"} <= found

    def test_bold_and_italic_round_trip(self, blocks_pdf) -> None:
        blocks = [
            FormattedBlock(BlockType.DIALOGUE, "Plainmarker text"),
            FormattedBlock(BlockType.DIALOGUE, "Boldmarker text", bold=True),
            FormattedBlock(BlockType.DIALOGUE, "Italicmarker text", italic=True),
        ]
        script = extract_lines(blocks_pdf(blocks))
        by_marker = {
            line.text.split()[0]: line for line in script.lines if line.text.split()
        }
        assert by_marker["Boldmarker"].bold is True
        assert by_marker["Italicmarker"].italic is True
        assert by_marker["Plainmarker"].bold is False
        assert by_marker["Plainmarker"].italic is False

    def test_indent_is_relative_and_monotonic(self, blocks_pdf) -> None:
        blocks = [
            FormattedBlock(BlockType.PROSE, "Flushleft marker", indent_level=0),
            FormattedBlock(BlockType.DIALOGUE, "Onetab marker", indent_level=1),
            FormattedBlock(BlockType.QUOTED_TEXT, "Twotab marker", indent_level=2),
        ]
        script = extract_lines(blocks_pdf(blocks))
        got = {
            line.text.split()[0]: line.indent
            for line in script.lines
            if line.text.split()
        }
        assert got["Flushleft"] == 0, "leftmost line must be indent 0"
        assert got["Flushleft"] < got["Onetab"] < got["Twotab"]

    def test_size_ratio_is_one_for_body_and_larger_for_headers(
        self, blocks_pdf
    ) -> None:
        blocks = [FormattedBlock(BlockType.PROSE, f"Body line {i}") for i in range(20)]
        blocks.insert(0, FormattedBlock(BlockType.SECTION_HEADER, "Bigheader marker"))
        script = extract_lines(blocks_pdf(blocks))
        body = [line for line in script.lines if line.text.startswith("Body")]
        header = next(line for line in script.lines if line.text.startswith("Bigheader"))
        assert all(line.size_ratio == pytest.approx(1.0) for line in body)
        assert header.size_ratio > 1.0

    def test_gap_before_marks_paragraph_breaks_only(self, blocks_pdf) -> None:
        blocks = [
            FormattedBlock(BlockType.PROSE, "Alpha marker"),
            FormattedBlock(BlockType.PROSE, "Bravo marker"),
            FormattedBlock(BlockType.BLANK_LINE, ""),
            FormattedBlock(BlockType.PROSE, "Charlie marker"),
        ]
        script = extract_lines(blocks_pdf(blocks))
        got = {
            line.text.split()[0]: line.gap_before
            for line in script.lines
            if line.text.split()
        }
        assert got["Charlie"] is True, "a blank line must produce gap_before"
        assert got["Bravo"] is False, "consecutive lines must not"


class TestFailsLoudly:
    def test_a_pdf_with_no_text_layer_raises(self, tmp_path) -> None:
        import fitz

        doc = fitz.open()
        doc.new_page()
        empty = tmp_path / "blank.pdf"
        doc.save(str(empty))
        doc.close()

        with pytest.raises(ReadViewError, match="no extractable text"):
            extract_lines(empty)

    def test_a_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises(ReadViewError):
            extract_lines(tmp_path / "nope.pdf")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_readview_extract.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'vo_format.readview'`

- [ ] **Step 4: Create the package marker**

Create `vo_format/readview/__init__.py`:

```python
"""Teleprompter read-view: turn a finished ColdRead PDF into a scrolling page."""

from __future__ import annotations

from .extract import ReadLine, ReadScript, ReadViewError, extract_lines

__all__ = ["ReadLine", "ReadScript", "ReadViewError", "extract_lines"]
```

- [ ] **Step 5: Implement extract.py**

Create `vo_format/readview/extract.py`:

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_readview_extract.py -q`
Expected: PASS, all tests.

The indent assertion is known-good: this exact three-block PDF was measured on
2026-07-30 and produced `x0` = 114.00 / 150.00 / 186.00 at 16pt, so the buckets
land on 0 / 4 / 8 character widths. If it nonetheless fails, do **not** loosen
the assertion — something changed in `pdf_writer.py`'s indents, and that is the
finding to report.

- [ ] **Step 7: Verify the tests are not vacuous**

Assertions in this repo have passed for the wrong reason before, so check by
mutation rather than by reading:

```bash
cp vo_format/readview/extract.py /tmp/extract.bak
# Mutation 1: break relative indent — use absolute x0
sed -i 's|indent=round((line.x0 - min_x0) / char_width)|indent=round(line.x0 / char_width)|' vo_format/readview/extract.py
./.venv/bin/python -m pytest tests/test_readview_extract.py -q   # MUST fail
cp /tmp/extract.bak vo_format/readview/extract.py
# Mutation 2: make gap_before always False
sed -i 's|prev.page == line.page and (line.y0 - prev.y0) > gap_threshold|False|' vo_format/readview/extract.py
./.venv/bin/python -m pytest tests/test_readview_extract.py -q   # MUST fail
cp /tmp/extract.bak vo_format/readview/extract.py
./.venv/bin/python -m pytest tests/test_readview_extract.py -q   # green again
```

Restore from the `cp` backup, never `git checkout` — that would discard all
uncommitted work in the file, not just the mutation.

- [ ] **Step 8: Lint and run the full suite**

```bash
./.venv/bin/ruff check vo_format/readview tests/
./.venv/bin/python -m pytest tests/ -q
```
Expected: no lint findings; the whole suite green.

- [ ] **Step 9: Commit**

```bash
git add vo_format/readview/__init__.py vo_format/readview/extract.py \
        tests/conftest.py tests/test_readview_extract.py
git commit -m "feat(readview): extract styled lines from a finished ColdRead PDF"
```

---

### Task 2: Theme — the dark palette

**Files:**
- Create: `vo_format/readview/theme.py`
- Test: `tests/test_readview_theme.py`

**Interfaces:**
- Consumes: `vo_format.colors.PALETTE`, `NARRATOR_COLOR`, `STAGE_DIRECTION_COLOR`, `SOUND_CUE_COLOR` (read only — `colors.py` is not modified).
- Produces:
  - `DARK_BACKGROUND: str`
  - `DARK_MAP: dict[str, str]`
  - `dark_color(print_hex: str) -> str`
  - `relative_luminance(hex_color: str) -> float`
  - `contrast_ratio(a: str, b: str) -> float`

**Why the values are what they are:** the eight `DARK_MAP` character colors were
computed by holding each print color's hue and saturation fixed and solving for
the lightness that lands on an evenly-spaced target luminance, preserving the
print palette's own luminance *ordering*. A naive hand-picked set had blue, red,
purple and pink within 0.004 luminance of each other — indistinguishable in
grayscale, which destroys the property `colors.py` deliberately protects. Do not
"tidy" these hexes.

- [ ] **Step 1: Write the failing theme tests**

Create `tests/test_readview_theme.py`:

```python
"""The dark palette must stay legible and stay grayscale-distinguishable.

Speaker color is the voice-switch cue for a performer voicing every character,
so a palette edit that collapses two characters together is a functional bug,
not a cosmetic one.
"""

from __future__ import annotations

import pytest

from vo_format.colors import (
    NARRATOR_COLOR,
    PALETTE,
    SOUND_CUE_COLOR,
    STAGE_DIRECTION_COLOR,
)
from vo_format.readview.theme import (
    DARK_BACKGROUND,
    DARK_MAP,
    contrast_ratio,
    dark_color,
    relative_luminance,
)

WCAG_AA = 4.5
MIN_GRAYSCALE_GAP = 0.05


class TestCoverage:
    def test_every_color_colors_py_can_emit_is_mapped(self) -> None:
        expected = {hex_value.lower() for hex_value, _name in PALETTE} | {
            NARRATOR_COLOR.lower(),
            STAGE_DIRECTION_COLOR.lower(),
            SOUND_CUE_COLOR.lower(),
        }
        assert expected <= set(DARK_MAP), (
            f"unmapped: {sorted(expected - set(DARK_MAP))}"
        )


class TestLegibility:
    @pytest.mark.parametrize("print_hex", sorted(DARK_MAP))
    def test_each_mapped_color_clears_wcag_aa(self, print_hex: str) -> None:
        ratio = contrast_ratio(DARK_MAP[print_hex], DARK_BACKGROUND)
        assert ratio >= WCAG_AA, f"{print_hex} -> {DARK_MAP[print_hex]} is {ratio:.2f}:1"

    def test_background_is_not_pure_black(self) -> None:
        # Pure black behind bright monospace haloes at booth distance.
        assert DARK_BACKGROUND.lower() != "#000000"
        assert relative_luminance(DARK_BACKGROUND) > 0.0

    def test_body_text_is_not_pure_white(self) -> None:
        assert DARK_MAP[NARRATOR_COLOR.lower()].lower() != "#ffffff"


class TestGrayscaleDistinctness:
    def test_character_colors_stay_separable_in_grayscale(self) -> None:
        lums = sorted(
            relative_luminance(DARK_MAP[hex_value.lower()])
            for hex_value, _name in PALETTE
        )
        gaps = [b - a for a, b in zip(lums, lums[1:])]
        assert min(gaps) >= MIN_GRAYSCALE_GAP, (
            f"closest pair differs by only {min(gaps):.4f}"
        )

    def test_dark_palette_preserves_the_print_luminance_ordering(self) -> None:
        print_order = [
            h for h, _ in sorted(PALETTE, key=lambda p: relative_luminance(p[0]))
        ]
        dark_order = [
            h
            for h, _ in sorted(
                PALETTE, key=lambda p: relative_luminance(DARK_MAP[p[0].lower()])
            )
        ]
        assert print_order == dark_order


class TestFallback:
    def test_an_unmapped_color_is_lifted_not_dropped(self) -> None:
        lifted = dark_color("#003366")  # a dark blue that is not in the palette
        assert lifted.lower() != "#003366"
        assert contrast_ratio(lifted, DARK_BACKGROUND) >= WCAG_AA

    def test_an_already_light_unmapped_color_is_left_alone(self) -> None:
        assert dark_color("#F5F5F5").lower() == "#f5f5f5"

    def test_lookup_is_case_insensitive(self) -> None:
        assert dark_color("#2563EB") == dark_color("#2563eb")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_readview_theme.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'vo_format.readview.theme'`

- [ ] **Step 3: Implement theme.py**

Create `vo_format/readview/theme.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_readview_theme.py -q`
Expected: PASS, all tests.

- [ ] **Step 5: Verify the grayscale test is not vacuous**

```bash
cp vo_format/readview/theme.py /tmp/theme.bak
# Collapse two character colors to nearly the same luminance
sed -i 's|"#dc2626": "#ed9191",|"#dc2626": "#75a0f0",|' vo_format/readview/theme.py
./.venv/bin/python -m pytest tests/test_readview_theme.py -q   # MUST fail on the gap test
cp /tmp/theme.bak vo_format/readview/theme.py
./.venv/bin/python -m pytest tests/test_readview_theme.py -q   # green again
```

- [ ] **Step 6: Lint, full suite, commit**

```bash
./.venv/bin/ruff check vo_format/readview tests/
./.venv/bin/python -m pytest tests/ -q
git add vo_format/readview/theme.py tests/test_readview_theme.py
git commit -m "feat(readview): dark palette preserving hue, AA contrast and grayscale spread"
```

---

### Task 3: Assets — CSS and the keep-awake video

**Files:**
- Create: `vo_format/readview/reader.css`
- Create: `vo_format/readview/assets.py`
- Modify: `pyproject.toml` (package-data globs only)

**Interfaces:**
- Consumes: `vo_format.readview.theme.DARK_BACKGROUND`, `DARK_MAP` (referenced from CSS as literal values via render, not imported by CSS).
- Produces: `KEEP_AWAKE_MP4_BASE64: str` in `assets.py`. `reader.css` as a package data file, read by `render.py` in Task 5.

This task has no test of its own — the assets are exercised by Tasks 4 and 5.
It exists as a separate task because it is the only task that touches packaging,
and a reviewer can accept or reject the packaging change independently.

- [ ] **Step 1: Generate the keep-awake video and write assets.py**

The Screen Wake Lock API requires a secure context. Neither `file://` nor plain
HTTP on a LAN address is one, so `navigator.wakeLock` is undefined on both of
this feature's delivery paths. A muted looping inline video is the mechanism
that actually works, and it must be embedded as a data URI to keep the page
self-contained.

Generate it (2×2 px, 1 frame, ~1.5KB):

```bash
ffmpeg -hide_banner -loglevel error -y -f lavfi \
  -i color=c=black:s=2x2:d=1:r=1 \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart /tmp/awake.mp4
./.venv/bin/python - <<'PY'
import base64, pathlib, textwrap
data = base64.b64encode(pathlib.Path("/tmp/awake.mp4").read_bytes()).decode()
body = "\n".join(f'    "{chunk}"' for chunk in textwrap.wrap(data, 76))
pathlib.Path("vo_format/readview/assets.py").write_text(
    '"""Embedded binary assets for the read-view.\n\n'
    "The keep-awake video is a 2x2px, single-frame, silent H.264 clip. A muted\n"
    "looping inline <video> is what actually prevents an iPad from sleeping\n"
    "mid-read: the Screen Wake Lock API needs a secure context, which neither\n"
    "file:// nor plain HTTP on a LAN address provides.\n\n"
    "Regenerate with the ffmpeg command in\n"
    "docs/superpowers/plans/2026-07-30-pdf-teleprompter-phase1.md (Task 3).\n"
    '"""\n\nfrom __future__ import annotations\n\n'
    "KEEP_AWAKE_MP4_BASE64 = (\n" + body + "\n)\n"
)
print("wrote assets.py")
PY
```

- [ ] **Step 2: Verify the constant decodes to a valid MP4**

```bash
./.venv/bin/python -c "
import base64
from vo_format.readview.assets import KEEP_AWAKE_MP4_BASE64 as b
raw = base64.b64decode(b)
assert raw[4:8] == b'ftyp', raw[:16]
print(f'ok: {len(raw)} bytes, ftyp box present')"
```
Expected: `ok: ~1548 bytes, ftyp box present`

- [ ] **Step 3: Write reader.css**

Create `vo_format/readview/reader.css`. Colors are injected by `render.py` as
CSS custom properties, so this file carries no palette literals.

```css
:root {
  --bg: #121212;
  --fg: #e8e6e3;
  --chrome: rgba(255, 255, 255, 0.07);
  --font-size: 20px;
  --line-height: 1.55;
}

:root[data-theme="light"] {
  --bg: #ffffff;
  --fg: #000000;
  --chrome: rgba(0, 0, 0, 0.07);
}

* { box-sizing: border-box; }

html {
  background: var(--bg);
  /* The scroll loop sets scroll position every frame; native smoothing would
     fight it. */
  scroll-behavior: auto;
}

body {
  margin: 0;
  padding: 50vh 32px;          /* half a viewport of lead-in and run-out */
  background: var(--bg);
  color: var(--fg);
  font-family: "Courier New", Courier, monospace;
  font-size: var(--font-size);
  line-height: var(--line-height);
  -webkit-text-size-adjust: none;   /* Safari must not resize our text for us */
  -webkit-user-select: none;
  user-select: none;
  overscroll-behavior: none;
  touch-action: none;               /* we drive scrolling ourselves */
}

#script { max-width: 100%; }

.l {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: break-word;
  /* A breath group that wraps must still read as one unit. */
  padding-left: 2ch;
  text-indent: -2ch;
}

.l.gap { margin-top: var(--line-height); }
.l.b { font-weight: 700; }
.l.i { font-style: italic; }

/* Speed zones: full-height edge strips, deliberately unlabelled and always in
   the same place so they can be hit without looking. */
.zone {
  position: fixed;
  top: 0;
  bottom: 0;
  width: 15vw;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  color: var(--fg);
  opacity: 0.22;
  background: transparent;
  -webkit-tap-highlight-color: transparent;
}
.zone:active { background: var(--chrome); opacity: 0.5; }
#slower { left: 0; }
#faster { right: 0; }

#hud {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  z-index: 11;
  display: flex;
  gap: 14px;
  align-items: center;
  justify-content: center;
  padding: 10px 12px;
  background: var(--bg);
  border-top: 1px solid var(--chrome);
  font-size: 15px;
  opacity: 0.85;
}

#hud button {
  font: inherit;
  color: var(--fg);
  background: var(--chrome);
  border: 0;
  border-radius: 6px;
  padding: 8px 12px;
  min-width: 44px;              /* Apple's minimum comfortable touch target */
  -webkit-tap-highlight-color: transparent;
}

#status { min-width: 11ch; text-align: center; font-variant-numeric: tabular-nums; }

#awake {
  position: fixed;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}
```

- [ ] **Step 4: Add the package-data globs**

Modify `pyproject.toml`. Find:

```toml
[tool.setuptools.package-data]
vo_format = ["samples/*.md"]
```

Replace with:

```toml
[tool.setuptools.package-data]
vo_format = ["samples/*.md", "readview/*.css", "readview/*.js"]
```

And in `[tool.setuptools]`, find:

```toml
packages = ["vo_format"]
```

Replace with:

```toml
packages = ["vo_format", "vo_format.readview"]
```

Without the second change the new subpackage is not installed at all, and
`coldread-readview` would work only from a source checkout.

- [ ] **Step 5: Verify the package still installs and the assets are present**

```bash
./.venv/bin/python -m pip install -e . --quiet --no-deps
./.venv/bin/python -c "
from importlib.resources import files
d = files('vo_format.readview')
print('css bytes:', len((d / 'reader.css').read_text()))
from vo_format.readview.assets import KEEP_AWAKE_MP4_BASE64
print('mp4 b64 chars:', len(KEEP_AWAKE_MP4_BASE64))"
```
Expected: both non-zero.

**Do not run this install command from any directory other than this repo** — an
editable install run from a scratch clone silently repoints this venv's `.pth`
at the temp directory.

- [ ] **Step 6: Commit**

```bash
./.venv/bin/ruff check vo_format/readview tests/
./.venv/bin/python -m pytest tests/ -q
git add vo_format/readview/reader.css vo_format/readview/assets.py pyproject.toml
git commit -m "feat(readview): reader stylesheet and embedded keep-awake asset"
```

---

### Task 4: Reader behavior — the JavaScript

**Files:**
- Create: `vo_format/readview/reader.js`

**Interfaces:**
- Consumes: DOM produced by `render.py` (Task 5). The contract between them, which Task 5 must satisfy exactly:
  - `<body data-words-per-line="7.02" data-title="…">`
  - `<div id="script">` containing `<p class="l">` elements, optionally with `gap`, `b`, `i` classes
  - `<div class="zone" id="slower">`, `<div class="zone" id="faster">`
  - `<div id="hud">` containing `<button id="play">`, `<button id="smaller">`, `<button id="bigger">`, `<button id="theme">`, `<span id="status">`
  - `<video id="awake" muted loop playsinline>` with a `data:video/mp4;base64,…` source
- Produces: `reader.js` as package data, inlined by Task 5.

There is no unit test here: the arithmetic that can be *wrong* was deliberately
pushed into Python (Task 5 computes `words_per_line`), leaving this file as DOM
plumbing that only a real browser can validate. It is verified on-device in
Task 6.

- [ ] **Step 1: Write reader.js**

Create `vo_format/readview/reader.js`:

```javascript
"use strict";
(function () {
  var WPM_MIN = 40, WPM_MAX = 400, WPM_STEP = 5, WPM_DEFAULT = 150;
  var SIZE_MIN = 12, SIZE_MAX = 48, SIZE_STEP = 2, SIZE_DEFAULT = 20;

  var body = document.body;
  var wordsPerLine = parseFloat(body.dataset.wordsPerLine) || 7;
  var storeKey = "coldread:" + (body.dataset.title || "untitled");

  var el = {
    play: document.getElementById("play"),
    slower: document.getElementById("slower"),
    faster: document.getElementById("faster"),
    smaller: document.getElementById("smaller"),
    bigger: document.getElementById("bigger"),
    theme: document.getElementById("theme"),
    status: document.getElementById("status"),
    awake: document.getElementById("awake"),
    firstLine: document.querySelector(".l")
  };

  // Safari blocks storage for file:// origins, and Phase 1 is delivered as a
  // file. Losing preferences is acceptable; refusing to render is not.
  var store = {
    get: function (key, fallback) {
      try {
        var raw = localStorage.getItem(storeKey + ":" + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (e) { return fallback; }
    },
    set: function (key, value) {
      try { localStorage.setItem(storeKey + ":" + key, JSON.stringify(value)); }
      catch (e) { /* in-memory only */ }
    }
  };

  var wpm = store.get("wpm", WPM_DEFAULT);
  var size = store.get("size", SIZE_DEFAULT);
  var theme = store.get("theme", "dark");
  var pos = store.get("pos", 0);
  var running = false;
  var held = false;
  var lastFrame = 0;
  var dragStartY = 0, dragStartPos = 0;

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function lineHeightPx() {
    // Measured, not assumed: it changes with font size and orientation.
    if (!el.firstLine) { return size * 1.55; }
    var h = el.firstLine.getBoundingClientRect().height;
    return h > 0 ? h : size * 1.55;
  }

  function pxPerSecond() {
    var linesPerSecond = (wpm / 60) / wordsPerLine;
    return linesPerSecond * lineHeightPx();
  }

  function maxScroll() {
    return Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
  }

  function applySize() {
    document.documentElement.style.setProperty("--font-size", size + "px");
  }

  function applyTheme() {
    document.documentElement.setAttribute("data-theme", theme);
    el.theme.textContent = theme === "dark" ? "☾" : "☀";
  }

  function paintStatus() {
    el.status.textContent = wpm + " wpm" + (running ? "" : " ▌▌");
    el.play.textContent = running ? "▌▌" : "▶";
  }

  function seek(next) {
    pos = clamp(next, 0, maxScroll());
    window.scrollTo(0, pos);
  }

  function frame(now) {
    if (!running) { return; }
    if (lastFrame) {
      var dt = (now - lastFrame) / 1000;
      // A backgrounded tab returns a huge delta; skipping ahead a page would be
      // worse than losing the interval.
      if (dt > 0 && dt < 0.5 && !held) { seek(pos + pxPerSecond() * dt); }
    }
    lastFrame = now;
    if (pos >= maxScroll()) { pause(); return; }
    requestAnimationFrame(frame);
  }

  function play() {
    if (running) { return; }
    running = true;
    lastFrame = 0;
    keepAwake(true);
    paintStatus();
    requestAnimationFrame(frame);
  }

  function pause() {
    running = false;
    keepAwake(false);
    paintStatus();
    store.set("pos", pos);
  }

  function toggle() { running ? pause() : play(); }

  function keepAwake(on) {
    if (on) {
      var playing = el.awake.play();
      if (playing && playing.catch) { playing.catch(function () {}); }
      if (navigator.wakeLock && navigator.wakeLock.request) {
        // Only available over a real secure context; harmless when it is not.
        navigator.wakeLock.request("screen").catch(function () {});
      }
    } else {
      el.awake.pause();
    }
  }

  function nudgeWpm(delta) {
    wpm = clamp(wpm + delta, WPM_MIN, WPM_MAX);
    store.set("wpm", wpm);
    paintStatus();
  }

  function nudgeSize(delta) {
    size = clamp(size + delta, SIZE_MIN, SIZE_MAX);
    store.set("size", size);
    applySize();
  }

  // --- touch: down freezes, drag repositions, lift resumes ------------------
  document.addEventListener("touchstart", function (e) {
    if (e.target.closest("#hud, .zone")) { return; }
    held = true;
    dragStartY = e.touches[0].clientY;
    dragStartPos = pos;
  }, { passive: true });

  document.addEventListener("touchmove", function (e) {
    if (!held) { return; }
    e.preventDefault();               // stop native momentum fighting us
    seek(dragStartPos - (e.touches[0].clientY - dragStartY));
  }, { passive: false });

  document.addEventListener("touchend", function () {
    if (!held) { return; }
    held = false;
    lastFrame = 0;                    // do not credit the held time as elapsed
    store.set("pos", pos);
  }, { passive: true });

  // --- pointer (Pi screen, desktop) ----------------------------------------
  window.addEventListener("scroll", function () {
    if (!running && !held) { pos = window.scrollY; }
  }, { passive: true });

  // --- keyboard: also the foot-pedal path, and the Pi may have no touch -----
  window.addEventListener("keydown", function (e) {
    switch (e.key) {
      case " ": case "Enter": e.preventDefault(); toggle(); break;
      case "ArrowUp": e.preventDefault(); nudgeWpm(WPM_STEP); break;
      case "ArrowDown": e.preventDefault(); nudgeWpm(-WPM_STEP); break;
      case "PageDown": e.preventDefault(); seek(pos + window.innerHeight * 0.8); break;
      case "PageUp": e.preventDefault(); seek(pos - window.innerHeight * 0.8); break;
      case "Home": e.preventDefault(); seek(0); break;
      case "End": e.preventDefault(); seek(maxScroll()); break;
      default: break;
    }
  });

  el.play.addEventListener("click", toggle);
  el.slower.addEventListener("click", function () { nudgeWpm(-WPM_STEP); });
  el.faster.addEventListener("click", function () { nudgeWpm(WPM_STEP); });
  el.smaller.addEventListener("click", function () { nudgeSize(-SIZE_STEP); });
  el.bigger.addEventListener("click", function () { nudgeSize(SIZE_STEP); });
  el.theme.addEventListener("click", function () {
    theme = theme === "dark" ? "light" : "dark";
    store.set("theme", theme);
    applyTheme();
  });

  window.addEventListener("pagehide", function () { store.set("pos", pos); });

  applySize();
  applyTheme();
  seek(pos);
  paintStatus();
})();
```

- [ ] **Step 2: Syntax-check it without a browser**

```bash
node --check vo_format/readview/reader.js && echo "syntax ok"
```
If `node` is unavailable, skip — Task 6 catches syntax errors on-device
immediately (a blank page with a console error).

- [ ] **Step 3: Commit**

```bash
git add vo_format/readview/reader.js
git commit -m "feat(readview): reader behavior — scroll loop, touch, keyboard, keep-awake"
```

---

### Task 5: Render — ReadScript to one self-contained HTML file

**Files:**
- Create: `vo_format/readview/render.py`
- Modify: `vo_format/readview/__init__.py` (add `render` to the re-exports)
- Test: `tests/test_readview_render.py`

**Interfaces:**
- Consumes: `ReadScript`, `ReadLine` (Task 1); `DARK_BACKGROUND`, `DARK_MAP`, `dark_color` (Task 2); `KEEP_AWAKE_MP4_BASE64` and `reader.css` (Task 3); `reader.js` (Task 4).
- Produces: `render(script: ReadScript) -> str`.

- [ ] **Step 1: Write the failing render tests**

Create `tests/test_readview_render.py`:

```python
"""The rendered page must be self-contained and structurally faithful.

"Self-contained" is not a nicety: the file is read in a booth off a tablet, and
any external reference is a blank line waiting to happen.
"""

from __future__ import annotations

import re

import pytest

from vo_format.models import Archetype
from vo_format.readview.extract import ReadLine, ReadScript, extract_lines
from vo_format.readview.render import render
from vo_format.readview.theme import DARK_MAP


def _script(lines: list[ReadLine], title: str = "Test Script") -> ReadScript:
    return ReadScript(
        title=title,
        lines=lines,
        word_count=sum(len(line.text.split()) for line in lines),
        page_count=1,
        derived="2026-07-30T12:00:00+00:00",
    )


def _line(text: str, **kwargs) -> ReadLine:
    defaults = dict(
        color="#000000",
        bold=False,
        italic=False,
        size_ratio=1.0,
        indent=0,
        gap_before=False,
    )
    defaults.update(kwargs)
    return ReadLine(text=text, **defaults)  # type: ignore[arg-type]


class TestSelfContainment:
    @pytest.fixture
    def html(self, sample_pdf) -> str:
        pdf_path, _ = sample_pdf(Archetype.MULTI_VOICE_DRAMA)
        return render(extract_lines(pdf_path))

    def test_no_external_script_or_stylesheet(self, html: str) -> None:
        assert "<script src" not in html
        assert "<link" not in html
        assert "@import" not in html

    def test_no_remote_urls(self, html: str) -> None:
        remote = re.findall(r"https?://[^\s\"'<>]+", html)
        assert not remote, f"external URL(s) in output: {remote[:3]}"

    def test_the_only_url_function_is_the_inline_video(self, html: str) -> None:
        for url in re.findall(r"url\(([^)]*)\)", html):
            assert url.strip("\"'").startswith("data:"), url

    def test_css_and_js_are_actually_inlined(self, html: str) -> None:
        assert "<style>" in html and "</style>" in html
        assert "<script>" in html and "</script>" in html
        assert "requestAnimationFrame" in html      # from reader.js
        assert "--line-height" in html              # from reader.css

    def test_keep_awake_video_is_embedded(self, html: str) -> None:
        assert 'id="awake"' in html
        assert "data:video/mp4;base64," in html


class TestStructure:
    def test_every_line_becomes_one_paragraph(self) -> None:
        html = render(_script([_line("alpha"), _line("bravo"), _line("charlie")]))
        assert len(re.findall(r'<p class="l[^"]*"', html)) == 3

    def test_gap_before_adds_the_gap_class(self) -> None:
        html = render(_script([_line("alpha"), _line("bravo", gap_before=True)]))
        assert re.search(r'<p class="l gap"[^>]*>bravo</p>', html)

    def test_bold_and_italic_become_classes(self) -> None:
        html = render(_script([_line("b", bold=True), _line("i", italic=True)]))
        assert 'class="l b"' in html
        assert 'class="l i"' in html

    def test_color_is_emitted_as_its_dark_counterpart(self) -> None:
        html = render(_script([_line("spoken", color="#2563EB")]))
        assert DARK_MAP["#2563eb"] in html
        assert "#2563eb" not in html.lower().split("<style>")[1].split("</style>")[0]

    def test_indent_is_emitted_in_character_units(self) -> None:
        html = render(_script([_line("deep", indent=4)]))
        assert "4ch" in html

    def test_size_ratio_is_emitted_for_non_body_lines(self) -> None:
        html = render(_script([_line("head", size_ratio=1.5), _line("body")]))
        assert "1.5em" in html

    def test_words_per_line_reaches_the_client(self) -> None:
        script = _script([_line("one two three"), _line("four five six")])
        assert 'data-words-per-line="3.0"' in render(script)

    def test_title_and_derived_date_are_shown(self) -> None:
        html = render(_script([_line("x")], title="Kingdom Hearts Dark Road"))
        assert "Kingdom Hearts Dark Road" in html
        assert "2026-07-30" in html


class TestEscaping:
    def test_html_metacharacters_in_script_text_are_escaped(self) -> None:
        html = render(_script([_line("<script>alert(1)</script> & \"quoted\"")]))
        assert "<script>alert(1)" not in html
        assert "&lt;script&gt;alert(1)" in html
        assert "&amp;" in html

    def test_metacharacters_in_the_title_are_escaped(self) -> None:
        html = render(_script([_line("x")], title='Ep "1" <draft> & more'))
        assert "<draft>" not in html
        assert "&lt;draft&gt;" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_readview_render.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'vo_format.readview.render'`

- [ ] **Step 3: Implement render.py**

Create `vo_format/readview/render.py`:

```python
"""Assemble a ReadScript into one self-contained teleprompter page.

Everything is inlined — CSS, JS, and the keep-awake video as a data URI — so the
file works with the network off, which is the only condition that matters in a
booth.
"""

from __future__ import annotations

from html import escape
from importlib.resources import files

from .assets import KEEP_AWAKE_MP4_BASE64
from .extract import ReadLine, ReadScript
from .theme import DARK_BACKGROUND, dark_color


def _asset(name: str) -> str:
    return (files(__package__) / name).read_text(encoding="utf-8")


def _line_html(line: ReadLine) -> str:
    classes = ["l"]
    if line.gap_before:
        classes.append("gap")
    if line.bold:
        classes.append("b")
    if line.italic:
        classes.append("i")

    styles: list[str] = []
    color = dark_color(line.color)
    # Body text takes its color from the theme's --fg so the light/dark toggle
    # can move it; only genuinely colored lines get a hard value.
    if color.lower() != dark_color("#000000").lower():
        styles.append(f"color:{color}")
    if line.indent:
        styles.append(f"margin-left:{line.indent}ch")
    if line.size_ratio != 1.0:
        styles.append(f"font-size:{line.size_ratio:g}em")

    attrs = f' style="{";".join(styles)}"' if styles else ""
    return f'<p class="{" ".join(classes)}"{attrs}>{escape(line.text)}</p>'


def render(script: ReadScript) -> str:
    """Render `script` as one self-contained HTML document."""
    title = escape(script.title)
    lines = "\n".join(_line_html(line) for line in script.lines)
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, \
maximum-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="{DARK_BACKGROUND}">
<title>{title}</title>
<style>
{_asset("reader.css")}
</style>
</head>
<body data-words-per-line="{script.words_per_line:.4g}" data-title="{title}">
<div class="zone" id="slower">&minus;</div>
<div class="zone" id="faster">+</div>
<div id="script">
<p class="l b" style="font-size:1.4em">{title}</p>
<p class="l i">{len(script.lines)} lines &middot; {script.word_count} words \
&middot; derived {escape(script.derived[:10])}</p>
{lines}
</div>
<div id="hud">
<button id="smaller" type="button" aria-label="Smaller text">A&minus;</button>
<button id="bigger" type="button" aria-label="Larger text">A+</button>
<button id="play" type="button" aria-label="Play or pause">&#9654;</button>
<span id="status"></span>
<button id="theme" type="button" aria-label="Toggle light or dark">&#9790;</button>
</div>
<video id="awake" muted loop playsinline preload="auto"
 src="data:video/mp4;base64,{KEEP_AWAKE_MP4_BASE64}"></video>
<script>
{_asset("reader.js")}
</script>
</body>
</html>
"""
```

- [ ] **Step 4: Add render to the package re-exports**

Modify `vo_format/readview/__init__.py` to read:

```python
"""Teleprompter read-view: turn a finished ColdRead PDF into a scrolling page."""

from __future__ import annotations

from .extract import ReadLine, ReadScript, ReadViewError, extract_lines
from .render import render

__all__ = [
    "ReadLine",
    "ReadScript",
    "ReadViewError",
    "extract_lines",
    "render",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_readview_render.py -q`
Expected: PASS, all tests.

- [ ] **Step 6: Verify the self-containment tests are not vacuous**

```bash
cp vo_format/readview/render.py /tmp/render.bak
sed -i 's|<style>|<link rel="stylesheet" href="https://cdn.example/x.css"><style>|' \
  vo_format/readview/render.py
./.venv/bin/python -m pytest tests/test_readview_render.py -q   # MUST fail
cp /tmp/render.bak vo_format/readview/render.py
./.venv/bin/python -m pytest tests/test_readview_render.py -q   # green again
```

- [ ] **Step 7: Lint, full suite, commit**

```bash
./.venv/bin/ruff check vo_format/readview tests/
./.venv/bin/python -m pytest tests/ -q
git add vo_format/readview/render.py vo_format/readview/__init__.py \
        tests/test_readview_render.py
git commit -m "feat(readview): render a ReadScript to one self-contained HTML page"
```

---

### Task 6: CLI — `coldread-readview`

**Files:**
- Create: `vo_format/readview/cli.py`
- Modify: `pyproject.toml` (`[project.scripts]`)
- Test: `tests/test_readview_cli.py`

**Interfaces:**
- Consumes: `extract_lines`, `render`, `ReadViewError`.
- Produces: `main(argv: list[str] | None = None) -> int`, `readview_path_for(pdf: Path) -> Path`.

`main` returns an exit code rather than calling `sys.exit`, so it is testable.
A thin `_entry()` wrapper does the exiting for the console script.

- [ ] **Step 1: Write the failing CLI tests**

Create `tests/test_readview_cli.py`:

```python
"""CLI behavior, with an emphasis on refusing to fail quietly.

This repo has shipped silent content loss at exit 0 twice (issues #138, #139).
An empty or missing read-view must be loud.
"""

from __future__ import annotations

from pathlib import Path

from vo_format.models import Archetype
from vo_format.readview.cli import main, readview_path_for


class TestHappyPath:
    def test_writes_an_html_file_beside_the_pdf(self, sample_pdf, capsys) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        assert main([str(pdf_path)]) == 0
        out = readview_path_for(pdf_path)
        assert out.is_file()
        assert out.read_text(encoding="utf-8").startswith("<!doctype html>")

    def test_output_name_follows_the_formatted_convention(self, tmp_path) -> None:
        pdf = tmp_path / "Kingdom Hearts Dark Road - formatted.pdf"
        assert readview_path_for(pdf).name == (
            "Kingdom Hearts Dark Road - readview.html"
        )

    def test_a_pdf_without_the_suffix_still_gets_a_sane_name(self, tmp_path) -> None:
        assert readview_path_for(tmp_path / "Ep1.pdf").name == "Ep1 - readview.html"

    def test_prints_the_line_count_canary(self, sample_pdf, capsys) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        main([str(pdf_path)])
        captured = capsys.readouterr().out
        assert "extracted" in captured
        assert "lines from" in captured
        assert "pages" in captured

    def test_accepts_several_pdfs_at_once(self, sample_pdf) -> None:
        first, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        second, _ = sample_pdf(Archetype.MULTI_VOICE_DRAMA)
        assert main([str(first), str(second)]) == 0
        assert readview_path_for(first).is_file()
        assert readview_path_for(second).is_file()


class TestIdempotence:
    def test_skips_when_the_html_is_newer_than_the_pdf(
        self, sample_pdf, capsys
    ) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        main([str(pdf_path)])
        out = readview_path_for(pdf_path)
        first = out.read_text(encoding="utf-8")
        out.write_text("SENTINEL", encoding="utf-8")

        assert main([str(pdf_path)]) == 0
        assert out.read_text(encoding="utf-8") == "SENTINEL"
        assert "skip" in capsys.readouterr().out.lower()
        assert first  # the first render did happen

    def test_force_rewrites_regardless(self, sample_pdf) -> None:
        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        main([str(pdf_path)])
        out = readview_path_for(pdf_path)
        out.write_text("SENTINEL", encoding="utf-8")

        assert main([str(pdf_path), "--force"]) == 0
        assert out.read_text(encoding="utf-8") != "SENTINEL"

    def test_rerenders_when_the_pdf_is_newer(self, sample_pdf) -> None:
        import os
        import time

        pdf_path, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        main([str(pdf_path)])
        out = readview_path_for(pdf_path)
        out.write_text("SENTINEL", encoding="utf-8")
        # Touch the PDF into the future so it is unambiguously newer.
        future = time.time() + 60
        os.utime(pdf_path, (future, future))

        assert main([str(pdf_path)]) == 0
        assert out.read_text(encoding="utf-8") != "SENTINEL"


class TestFailsLoudly:
    def test_a_textless_pdf_exits_nonzero_and_writes_nothing(
        self, tmp_path, capsys
    ) -> None:
        import fitz

        doc = fitz.open()
        doc.new_page()
        blank = tmp_path / "blank.pdf"
        doc.save(str(blank))
        doc.close()

        assert main([str(blank)]) != 0
        assert not readview_path_for(blank).exists()
        assert "no extractable text" in capsys.readouterr().err

    def test_a_missing_file_exits_nonzero(self, tmp_path, capsys) -> None:
        assert main([str(tmp_path / "nope.pdf")]) != 0
        assert capsys.readouterr().err.strip()

    def test_one_bad_file_does_not_stop_the_others(
        self, sample_pdf, tmp_path
    ) -> None:
        good, _ = sample_pdf(Archetype.SINGLE_NARRATOR)
        bad = tmp_path / "missing.pdf"

        assert main([str(bad), str(good)]) != 0, "must still report failure"
        assert readview_path_for(good).is_file(), "the good file must be rendered"

    def test_no_arguments_exits_nonzero(self) -> None:
        assert main([]) != 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_readview_cli.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'vo_format.readview.cli'`

- [ ] **Step 3: Implement cli.py**

Create `vo_format/readview/cli.py`:

```python
"""`coldread-readview` — turn finished ColdRead PDFs into teleprompter pages.

Deliberately separate from vo_format.cli: that module is a single flat parser
with a positional `script` argument, and bolting subparsers onto it would break
the existing `coldread <script>` invocation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .extract import ReadViewError, extract_lines
from .render import render

_PDF_SUFFIX = " - formatted"
_OUT_SUFFIX = " - readview"


def readview_path_for(pdf: Path) -> Path:
    """The HTML path for a PDF, mirroring the `- formatted.pdf` convention."""
    stem = pdf.stem
    for suffix in (_PDF_SUFFIX, " - batched"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return pdf.with_name(f"{stem}{_OUT_SUFFIX}.html")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coldread-readview",
        description=(
            "Derive a self-contained auto-scrolling teleprompter page from an "
            "already-formatted ColdRead PDF."
        ),
    )
    parser.add_argument("pdfs", nargs="*", type=Path, help="PDF file(s) to convert")
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-render even when the existing HTML is newer than its PDF",
    )
    return parser


def _convert(pdf: Path, force: bool) -> None:
    """Convert one PDF. Raises ReadViewError on any failure."""
    if not pdf.is_file():
        raise ReadViewError(f"{pdf}: not a file")

    out = readview_path_for(pdf)
    if not force and out.exists() and out.stat().st_mtime >= pdf.stat().st_mtime:
        print(f"skip  {out.name} (newer than its PDF)")
        return

    script = extract_lines(pdf)
    # The canary: line count tracks the script, so a three-digit drop is visible.
    print(
        f"ok    {out.name} — extracted {len(script.lines)} lines "
        f"from {script.page_count} pages, {script.word_count} words"
    )
    out.write_text(render(script), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.pdfs:
        print("error: no PDF given", file=sys.stderr)
        return 2

    failures = 0
    for pdf in args.pdfs:
        try:
            _convert(pdf, force=args.force)
        except ReadViewError as exc:
            failures += 1
            print(f"error: {exc}", file=sys.stderr)
    return 1 if failures else 0


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
```

- [ ] **Step 4: Register the entry point**

Modify `pyproject.toml`. Find:

```toml
[project.scripts]
coldread = "vo_format.cli:main"
```

Replace with:

```toml
[project.scripts]
coldread = "vo_format.cli:main"
coldread-readview = "vo_format.readview.cli:_entry"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_readview_cli.py -q`
Expected: PASS, all tests.

- [ ] **Step 6: Verify the loud-failure tests are not vacuous**

```bash
cp vo_format/readview/cli.py /tmp/cli.bak
# Make failures silent and successful — the exact bug shape of #138
sed -i 's|return 1 if failures else 0|return 0|' vo_format/readview/cli.py
./.venv/bin/python -m pytest tests/test_readview_cli.py -q   # MUST fail
cp /tmp/cli.bak vo_format/readview/cli.py
./.venv/bin/python -m pytest tests/test_readview_cli.py -q   # green again
```

- [ ] **Step 7: Lint, full suite, commit**

```bash
./.venv/bin/ruff check vo_format/readview tests/
./.venv/bin/python -m pytest tests/ -q
git add vo_format/readview/cli.py pyproject.toml tests/test_readview_cli.py
git commit -m "feat(readview): add the coldread-readview CLI entry point"
```

---

### Task 7: Real-PDF verification, on-device check, and docs

**Files:**
- Modify: `README.md`
- No new code. This task is where the feature is proven against reality rather than against fixtures.

**Interfaces:**
- Consumes: everything above.
- Produces: a verified, documented feature.

- [ ] **Step 1: Reinstall so the new entry point exists**

```bash
./.venv/bin/python -m pip install -e . --quiet --no-deps
./.venv/bin/coldread-readview --help
```
Expected: the help text prints.

- [ ] **Step 2: Run against all ten real PDFs**

```bash
./.venv/bin/coldread-readview \
  "$HOME"/OneDrive/CL/ready/*.pdf "$HOME"/OneDrive/BoP/ready/*.pdf
echo "exit: $?"
```

Expected: ten `ok` lines, exit 0. **Check the line counts against these
measured values** — a materially lower number means content was lost:

| PDF | Expected lines | Pages |
| --- | --- | --- |
| `The Complete Story of Kingdom Hearts Dark Road - formatted.pdf` | 1030 | 34 |
| `Bloodborne Ep1 - Blood Ministry - batched.pdf` | 768 | 24 |
| `Disco Elysium Ep1 - The Officer in the Brown Coat - formatted.pdf` | 1062 | 33 |

- [ ] **Step 3: Confirm the output is genuinely offline-capable**

```bash
f="$HOME/OneDrive/BoP/ready/The Complete Story of Kingdom Hearts Dark Road - readview.html"
grep -c 'https\?://' "$f" || echo "0 remote URLs (grep found none)"
grep -c '<script src\|<link ' "$f" || echo "0 external includes"
ls -lh "$f"
```
Expected: zero remote URLs, zero external includes.

- [ ] **Step 4: Render it in a real browser on this machine**

`--virtual-time-budget` matters here: without it the capture happens before
`reader.js` has applied the theme and font size, and the screenshot lies.

```bash
/usr/bin/brave-browser --headless=new --disable-gpu --no-sandbox \
  --hide-scrollbars --force-color-profile=srgb --virtual-time-budget=5000 \
  --window-size=834,1194 --screenshot=/tmp/readview.png \
  "file://$HOME/OneDrive/BoP/ready/The Complete Story of Kingdom Hearts Dark Road - readview.html"
```

Then view `/tmp/readview.png`. Expected: near-black background, off-white
monospace, the two faint edge strips, and the HUD along the bottom showing
`150 wpm ▌▌`. If the page is blank or unstyled, `reader.js` threw — run the same
URL non-headless and read the console.

- [ ] **Step 5: Verify on the iPad — the part fixtures cannot cover**

Wait for OneDrive to sync, then on the iPad open Files → OneDrive → `BoP/ready/`
→ the `- readview.html` file. Confirm each of these, and report any that fail
rather than working around them:

- [ ] It opens and renders dark with large monospace type
- [ ] `▶` starts a slow downward scroll
- [ ] A finger held on the text freezes it; dragging moves it; lifting resumes
- [ ] The `−` / `+` edge strips change the wpm readout
- [ ] `A−` / `A+` change type size and lines still fit portrait at 20px
- [ ] The theme button switches to light and back
- [ ] **The screen does not sleep during a two-minute unattended scroll** — this
      is the keep-awake video doing its job, and the one thing most likely to
      differ by iOS version
- [ ] With a Bluetooth keyboard or foot pedal: space pauses and resumes

- [ ] **Step 6: Document it in the README**

Add this section to `README.md`, after the CLI usage section:

```markdown
## Teleprompter read-view

`coldread-readview` turns an already-formatted ColdRead PDF into a single
self-contained HTML page that auto-scrolls, for reading aloud off a tablet.

```bash
coldread-readview "path/to/My Script - formatted.pdf"
# writes "path/to/My Script - readview.html"
```

It reads the PDF's embedded text layer, so speaker colors, bold and italic, the
size hierarchy, indentation, and the cold-read breath-group line breaks all
carry over exactly. Pages do not: the result is one continuous scroll with no
page seams.

The output has no external references of any kind — all CSS, JS, and the
keep-awake video are inlined — so it works with the network off.

**Controls:** hold a finger on the text to freeze it, drag to reposition, lift to
resume. The unlabelled left and right edge strips step the speed in words per
minute. `A−`/`A+` set type size. Space, arrow keys and Page Up/Down do the same
from a keyboard, which is also how a Bluetooth foot pedal reaches it.

Re-running skips any page whose HTML is already newer than its PDF; pass
`--force` to re-render anyway.
```

- [ ] **Step 7: Commit**

```bash
git add README.md
git commit -m "docs: document the coldread-readview teleprompter output"
```

---

## Plan self-review

**Spec coverage.** Walked each spec section against the tasks:

| Spec section | Covered by |
| --- | --- |
| Data model (`ReadLine`, `ReadScript`, all three relative derivations) | Task 1 |
| Dark palette, AA, grayscale, light-mode escape hatch | Task 2 (palette), Task 3 (light CSS), Task 4 (toggle) |
| Even brightness, no read-line, no dimming | Task 3 — `reader.css` has no highlight or dim rule at all |
| wpm speed model and its explicit conversion | Task 4 (`pxPerSecond`), Task 5 (`data-words-per-line`) |
| Touch / edge-zone / keyboard control | Task 4 |
| Keep-awake, all three layers | Task 3 (asset), Task 4 (video + wakeLock + Task 7 documents Auto-Lock) |
| `localStorage` with in-memory degradation | Task 4 (`store`) |
| Font size, 20px default, hanging indent on wrap | Task 3 (`.l` text-indent), Task 4 |
| Self-containment | Task 5 tests |
| Idempotence, canary, zero-lines hard failure | Task 6 |
| Verification on real PDFs and on-device | Task 7 |

**Deliberately deferred to Phase 2, per the spec:** `serve.py`, the library
index, token auth, `Host`/`Origin` checks, log scrubbing, the rsync publish step,
and the stdlib-only AST import test. None belong in this plan.

**Two spec items intentionally not implemented as written:**

1. The spec's `render(ReadScript) -> str` is honored, but the spec also implies
   the reader computes `words_per_line`. It is computed in Python instead
   (`ReadScript.words_per_line`) so the arithmetic is unit-tested rather than
   living only in untestable JS. The spec's stated formula is unchanged.
2. The spec lists a Playwright-based JS test as optional hardening. It is not in
   this plan — Task 7's headless screenshot plus the on-device checklist cover
   the same ground for a first release. Worth revisiting if the JS grows.

**Type consistency.** Checked every cross-task name: `ReadLine`/`ReadScript`
fields match between Tasks 1, 5 and their tests; `dark_color`,
`relative_luminance`, `contrast_ratio`, `DARK_MAP`, `DARK_BACKGROUND` match
between Tasks 2, 5 and the theme tests; `KEEP_AWAKE_MP4_BASE64` matches between
Tasks 3 and 5; every DOM id in `reader.js` (Task 4) appears in `render`'s
template (Task 5) — `slower`, `faster`, `play`, `smaller`, `bigger`, `theme`,
`status`, `awake`, `script`; `readview_path_for` and `main` match between Task 6
and its tests.

**Assumptions verified before writing, not left to the implementer:**

- `indent_level` 0/1/2 do produce distinguishable offsets — measured x0 = 114.00
  / 150.00 / 186.00, giving buckets 0 / 4 / 8.
- The dark palette's eleven colors all clear WCAG AA against `#121212` (lowest is
  the sound-cue gray at 4.83:1) and the eight character colors sit on an even
  luminance ladder with a minimum adjacent gap of 0.069.
- `ffmpeg` produces the keep-awake clip at 1548 bytes / 2064 base64 characters.
- `/usr/bin/brave-browser` and `/usr/bin/node` both exist on this machine, so
  Task 7 Step 4 and Task 4 Step 2 will run rather than being skipped.

**One risk that remains and cannot be pre-verified here:** whether the muted
inline video actually holds the iPad awake on the installed iOS version. That is
Task 7 Step 5's two-minute unattended scroll, and the documented Auto-Lock →
Never fallback exists precisely because this is the item most likely to differ by
device.
