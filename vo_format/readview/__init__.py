"""Teleprompter read-view: turn a finished ColdRead PDF into a scrolling page."""

from __future__ import annotations

from .extract import ReadLine, ReadScript, ReadViewError, extract_lines

__all__ = ["ReadLine", "ReadScript", "ReadViewError", "extract_lines"]
