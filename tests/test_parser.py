"""Parser extraction and normalization tests."""

from __future__ import annotations

import pytest

from vo_format.parser import MAX_FILE_BYTES, extract_text, normalize_text


def test_normalize_collapses_blank_lines():
    text = "a\n\n\n\n\nb\n"
    out = normalize_text(text)
    assert out == "a\n\n\nb\n"


def test_normalize_strips_bom_and_crlf():
    text = "\ufeffhello\r\nworld\r\n"
    out = normalize_text(text)
    assert not out.startswith("\ufeff")
    assert "\r" not in out
    assert out.endswith("\n")


def test_normalize_strips_base64_image_refs():
    text = "[image1]: <data:image/png;base64," + ("A" * 200) + ">\nreal line\n"
    out = normalize_text(text)
    assert "base64" not in out
    assert "real line" in out


def test_extract_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        extract_text("C:/definitely/not/a/real/path.md")


def test_extract_rejects_unsupported_extension(tmp_path):
    p = tmp_path / "foo.xyz"
    p.write_text("hello")
    with pytest.raises(ValueError):
        extract_text(str(p))


def test_extract_rejects_empty_file(tmp_path):
    p = tmp_path / "foo.txt"
    p.write_text("")
    with pytest.raises(ValueError):
        extract_text(str(p))


def test_extract_enforces_size_limit(tmp_path, monkeypatch):
    p = tmp_path / "huge.txt"
    p.write_text("x" * 10)
    # Lower the cap to force the guard to trigger.
    monkeypatch.setattr("vo_format.parser.MAX_FILE_BYTES", 5)
    with pytest.raises(ValueError, match="too large"):
        extract_text(str(p))


def test_extract_txt_roundtrip(tmp_path):
    p = tmp_path / "script.md"
    p.write_text("# Title\n\nBody\n", encoding="utf-8")
    text, ext = extract_text(str(p))
    assert ext == "md"
    assert "Title" in text
