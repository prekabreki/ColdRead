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

_OUT_SUFFIX = " - readview"


def readview_path_for(pdf: Path) -> Path:
    """The HTML path for a PDF.

    Derived from the FULL stem deliberately. An earlier version stripped a
    trailing " - formatted"/" - batched" first, which made two genuinely
    different documents (a formatted cut and a voice-batched cut of the same
    title) collide onto one filename and silently overwrite each other.
    Keeping the whole stem also means the variant stays visible in the
    filename, which is what tells you which cut you are reading.
    """
    return pdf.with_name(f"{pdf.stem}{_OUT_SUFFIX}.html")


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
    parser.add_argument(
        "--library",
        metavar="HREF",
        help=(
            "add a 'Library' button linking to HREF (e.g. index.html). Off by "
            "default: a lone read-view has no library to return to"
        ),
    )
    parser.add_argument(
        "--sync",
        metavar="HREF",
        help=(
            "share read state with other devices via the state service at HREF "
            "(e.g. /state). Off by default: a lone read-view has no service to "
            "talk to, and no page should issue a request nobody asked for"
        ),
    )
    return parser


def _convert(
    pdf: Path,
    force: bool,
    library: str | None = None,
    sync: str | None = None,
) -> None:
    """Convert one PDF. Raises ReadViewError on any failure."""
    if not pdf.is_file():
        raise ReadViewError(f"{pdf}: not a file")

    out = readview_path_for(pdf)
    # Strictly newer, not >=: a tie (coarse filesystem mtimes, or a PDF
    # re-derived within the same timestamp tick) must re-render rather than
    # risk skipping and serving a stale read-view while claiming success.
    if not force and out.exists() and out.stat().st_mtime > pdf.stat().st_mtime:
        print(f"skip  {out.name} (newer than its PDF)")
        return

    script = extract_lines(pdf)
    try:
        out.write_text(
            render(script, library=library, sync=sync), encoding="utf-8"
        )
    except OSError as exc:
        raise ReadViewError(f"{out.name}: could not write ({exc})") from exc
    # The canary: line count tracks the script, so a three-digit drop is
    # visible. Printed only after the write succeeds, so "ok" never claims
    # a file exists when it doesn't.
    print(
        f"ok    {out.name} — extracted {len(script.lines)} lines "
        f"from {script.page_count} pages, {script.word_count} words"
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.pdfs:
        print("error: no PDF given", file=sys.stderr)
        return 2

    failures = 0
    for pdf in args.pdfs:
        try:
            _convert(
                pdf, force=args.force, library=args.library, sync=args.sync
            )
        except ReadViewError as exc:
            failures += 1
            print(f"error: {exc}", file=sys.stderr)
    return 1 if failures else 0


def _entry() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    _entry()
