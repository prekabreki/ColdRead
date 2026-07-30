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
