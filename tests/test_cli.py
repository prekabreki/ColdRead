"""Tests for the CLI entry point — toggle-field agreement and argv parsing."""

from __future__ import annotations

import os
import sys

from vo_format.cli import _collect_cli_overrides, build_parser
from vo_format.toggles import TOGGLE_DEFINITIONS


def test_toggle_fields_derived_from_definitions():
    """Every TOGGLE_DEFINITIONS entry must have a corresponding argparse dest."""
    parser = build_parser()
    args = parser.parse_args([])
    toggle_names = {d["name"] for d in TOGGLE_DEFINITIONS}
    for name in toggle_names:
        assert hasattr(args, name), (
            f"Parser missing dest for toggle '{name}' "
            f"— add a CLI flag in build_parser()"
        )


def test_collect_cli_overrides_representative_argv():
    """Parse representative argv and verify _collect_cli_overrides returns values."""
    parser = build_parser()
    argv = [
        "--no-color-characters",
        "--narrator-style", "bold",
        "--no-section-breaks",
        "--breathing-marks",
        "--font-size", "18",
        "--line-spacing", "1.5",
        "--margins", "extra",
    ]
    args = parser.parse_args(argv)
    overrides = _collect_cli_overrides(args)

    assert overrides["color_characters"] is False
    assert overrides["narrator_style"] == "bold"
    assert overrides["section_breaks"] is False
    assert overrides["breathing_marks"] is True
    assert overrides["font_size"] == 18
    assert overrides["line_spacing"] == 1.5
    assert overrides["margins"] == "extra"

    assert "stage_directions" not in overrides
    assert "sound_cues" not in overrides
    assert "character_legend" not in overrides


def test_collect_cli_overrides_empty():
    """No CLI flags given → empty overrides dict."""
    parser = build_parser()
    args = parser.parse_args(["--list-samples"])
    overrides = _collect_cli_overrides(args)
    assert overrides == {}


def test_main_no_preflight_to_temp_pdf(tmp_path):
    """Drive main() with --no-preflight --non-interactive to produce a PDF."""
    sample = os.path.join(
        os.path.dirname(__file__), "..", "vo_format", "samples",
        "single_narrator_sample.md",
    )
    output = tmp_path / "test_output.pdf"

    test_argv = [
        "coldread",
        os.path.normpath(sample),
        "--no-preflight",
        "--non-interactive",
        "--archetype", "single_narrator",
        "-o", str(output),
    ]

    saved_argv = sys.argv
    saved_stdin = sys.stdin
    try:
        from io import StringIO
        sys.argv = test_argv
        sys.stdin = StringIO()
        from vo_format.cli import main
        main()
    except SystemExit:
        pass
    finally:
        sys.argv = saved_argv
        sys.stdin = saved_stdin

    assert output.exists(), f"Expected PDF at {output}"
    assert output.stat().st_size > 1000, (
        f"PDF too small ({output.stat().st_size} bytes) — likely corrupt"
    )
