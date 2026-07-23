"""Text extraction and normalization from script files."""

from __future__ import annotations

import os
import re

# Upper bounds to prevent OOM from adversarial or malformed input files.
MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB on disk
MAX_PDF_PAGES = 2_000
MAX_DOCX_PARAGRAPHS = 200_000


def _check_file_size(file_path: str) -> None:
    size = os.path.getsize(file_path)
    if size > MAX_FILE_BYTES:
        raise ValueError(f"Script file is too large ({size:,} bytes). Limit is {MAX_FILE_BYTES:,} bytes.")


def _extract_pdf(file_path: str) -> str:
    """Extract text from a PDF file using pymupdf."""
    try:
        import pymupdf
    except ImportError:
        raise ImportError("pymupdf is required for PDF input. Install it with: pip install pymupdf")

    doc = pymupdf.open(file_path)
    try:
        if doc.page_count > MAX_PDF_PAGES:
            raise ValueError(f"PDF has too many pages ({doc.page_count}). Limit is {MAX_PDF_PAGES}.")
        pages = [page.get_text() for page in doc]
    finally:
        doc.close()
    return "\n".join(pages)


def _extract_docx(file_path: str) -> str:
    """Extract text from a .docx file using python-docx."""
    try:
        from docx import Document
    except ImportError:
        raise ImportError("python-docx is required for .docx input. Install it with: pip install python-docx")

    doc = Document(file_path)
    paragraphs: list[str] = []
    for i, p in enumerate(doc.paragraphs):
        if i >= MAX_DOCX_PARAGRAPHS:
            raise ValueError(f"DOCX has too many paragraphs (>{MAX_DOCX_PARAGRAPHS}).")
        paragraphs.append(p.text)
    return "\n".join(paragraphs)


def extract_text(file_path: str) -> tuple[str, str]:
    """Read a script file and return (raw_text, file_type).

    Supported formats:
      - .txt  — read as-is
      - .md   — read as-is (markdown processing happens in the formatter)
      - .pdf  — extract text via pymupdf
      - .docx — extract text via python-docx

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file type is not supported.
        ImportError: If a required extraction library is not installed.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Script file not found: {file_path}")

    _check_file_size(file_path)

    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        raw_text = _extract_pdf(file_path)
    elif ext == ".docx":
        raw_text = _extract_docx(file_path)
    elif ext in (".txt", ".md"):
        # Try UTF-8 first, fall back to latin-1
        for encoding in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(file_path, encoding=encoding) as f:
                    raw_text = f.read()
                break
            except UnicodeDecodeError:
                continue
        else:
            # Last resort: replace errors
            with open(file_path, encoding="utf-8", errors="replace") as f:
                raw_text = f.read()
    else:
        raise ValueError(f"Unsupported file type '{ext}'. Supported formats: .txt, .md, .pdf, .docx")

    if not raw_text.strip():
        raise ValueError(f"Script file is empty: {file_path}")

    return raw_text, ext.lstrip(".")


def normalize_text(raw_text: str) -> str:
    """Normalize raw script text for processing.

    - Normalize line endings to \\n
    - Strip BOM if present
    - Strip embedded base64 data URIs and image reference definitions
    - Collapse runs of 3+ blank lines to 2 blank lines
    - Strip trailing whitespace per line
    - Ensure file ends with a single newline
    """
    text = raw_text

    # Strip BOM
    if text.startswith("\ufeff"):
        text = text[1:]

    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Strip markdown image reference definitions with data URIs
    # e.g. [image1]: <data:image/png;base64,iVBOR...>
    text = re.sub(
        r"^\[[\w\s-]+\]:\s*<data:[^>]+>\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )

    # Strip inline base64 data URIs (any remaining)
    text = re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=\s]{100,}", "", text)

    # Strip trailing whitespace per line
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)

    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    # Ensure single trailing newline
    text = text.rstrip("\n") + "\n"

    return text
