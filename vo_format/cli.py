"""CLI entry point for ColdRead."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from .colors import assign_colors
from .formatter import format_script
from .models import (
    Archetype,
    FormatToggles,
    MarginPreset,
    NarratorStyle,
    PreflightResult,
    QuotedTextStyle,
)
from .parser import extract_text, normalize_text
from .pdf_writer import generate_pdf
from .backend import VALID_BACKENDS, resolve_backend, run_diagnostic, run_preflight, run_pronunciation
from .preflight import PreflightError
from .preview import render_preview
from .toggles import (
    ARCHETYPE_DEFAULTS,
    TOGGLE_DEFINITIONS,
    resolve_toggles,
    toggles_to_display,
)


console = Console()


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coldread",
        description="Format voice-over scripts into cold-read optimized PDFs.",
    )

    parser.add_argument(
        "script_file",
        help="Path to the input script (.txt, .md, .pdf, .docx)",
    )
    parser.add_argument(
        "-o", "--output",
        help="Output PDF path (default: <script_name>_formatted.pdf)",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Skip Claude API analysis, use archetype defaults",
    )
    parser.add_argument(
        "--archetype",
        choices=[a.value for a in Archetype],
        help="Force archetype (useful with --no-preflight)",
    )
    parser.add_argument(
        "--api-key",
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--backend",
        choices=list(VALID_BACKENDS),
        help=(
            "Analysis backend: 'api' uses the Anthropic API directly "
            "(needs ANTHROPIC_API_KEY); 'claude-code' shells out to the "
            "local 'claude' CLI in --print mode and uses your Claude.ai "
            "subscription. Default: api if ANTHROPIC_API_KEY is set, else "
            "claude-code if 'claude' is on PATH. Override via VO_FORMAT_BACKEND."
        ),
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="Run diagnostic review after formatting (extra API call)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Skip interactive toggle review, accept defaults",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Show styled terminal preview instead of generating PDF",
    )

    # Toggle flags
    _add_bool_flag(parser, "color-characters", "color_characters",
                   "Assign distinct colors to each character's lines")
    _add_bool_flag(parser, "section-breaks", "section_breaks",
                   "Insert page breaks between sections")
    _add_bool_flag(parser, "stage-directions", "stage_directions",
                   "Style stage directions (grey italic)")
    _add_bool_flag(parser, "sound-cues", "sound_cues",
                   "Style sound/tape cues distinctly")
    _add_bool_flag(parser, "source-labels", "source_labels",
                   "Style document/media source labels as blocks")
    _add_bool_flag(parser, "strip-metadata", "strip_metadata",
                   "Remove production notes, YouTube titles, etc.")
    _add_bool_flag(parser, "title-page", "title_page",
                   "Generate a title page with script info")
    _add_bool_flag(parser, "character-legend", "character_legend",
                   "Add color-coded character legend")
    _add_bool_flag(parser, "breathing-marks", "breathing_marks",
                   "Insert [breath] markers at natural pause points")
    _add_bool_flag(parser, "pause-notation", "pause_notation",
                   "Convert ... and em-dashes to visual pause markers")
    _add_bool_flag(parser, "pronunciation-guide", "pronunciation_guide",
                   "Add AI-generated phonetic hints for flagged words (extra API call)")
    _add_bool_flag(parser, "voice-batch", "voice_batch",
                   "Reorder script by character for batch recording sessions")
    _add_bool_flag(parser, "cold-read-breaks", "cold_read_breaks",
                   "Break lines at natural phrase boundaries for easier cold reading")

    parser.add_argument(
        "--narrator-style",
        choices=["normal", "italic", "bold"],
        dest="narrator_style",
        help="Narrator text style",
    )
    parser.add_argument(
        "--quoted-text-style",
        choices=["indent", "italic", "indent+italic", "none"],
        dest="quoted_text_style",
        help="Styling for in-game quoted text",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        choices=[12, 14, 16, 18],
        dest="font_size",
        help="Base font size in points",
    )
    parser.add_argument(
        "--line-spacing",
        type=float,
        dest="line_spacing",
        help="Line spacing multiplier",
    )
    parser.add_argument(
        "--margins",
        choices=["normal", "wide", "extra", "narrow"],
        help="Margin preset",
    )

    return parser


def _add_bool_flag(
    parser: argparse.ArgumentParser,
    flag_name: str,
    dest: str,
    help_text: str,
) -> None:
    """Add a --flag / --no-flag boolean pair."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        f"--{flag_name}",
        action="store_true",
        dest=dest,
        default=None,
        help=help_text,
    )
    group.add_argument(
        f"--no-{flag_name}",
        action="store_false",
        dest=dest,
        help=f"Disable: {help_text}",
    )


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _display_preflight(
    preflight: PreflightResult,
    color_map: dict[str, str],
) -> None:
    """Display preflight results using Rich."""
    console.print()
    console.print(Panel.fit(
        "[bold]ColdRead[/bold]",
        border_style="cyan",
    ))
    console.print()

    # Archetype
    archetype_display = preflight.archetype.value.replace("_", " ").title()
    console.print(f"  Script type: [bold]{archetype_display}[/bold]")
    console.print()

    # Characters
    if preflight.characters:
        console.print(f"  Characters found: [bold]{len(preflight.characters)}[/bold]")
        for char in sorted(preflight.characters, key=lambda c: c.line_count, reverse=True):
            color = color_map.get(char.name, "#FFFFFF")
            console.print(f"    [{color}]#[/{color}] {char.name} ({char.line_count} lines)")
        console.print()

    # Sections
    if preflight.sections:
        console.print(f"  Sections: [bold]{len(preflight.sections)}[/bold] detected")
        for sec in preflight.sections[:5]:  # Show first 5
            console.print(f"    - {sec.title}")
        if len(preflight.sections) > 5:
            console.print(f"    ... and {len(preflight.sections) - 5} more")
        console.print()

    # Stage directions / sound cues
    features = []
    if preflight.detected_stage_directions:
        features.append("Stage directions detected")
    if preflight.detected_sound_cues:
        features.append("Sound cues detected")
    if features:
        for f in features:
            console.print(f"  {f}")
        console.print()

    # Pronunciation flags
    if preflight.pronunciation_flags:
        console.print(f"  Pronunciation flags: {len(preflight.pronunciation_flags)}")
        words = [p.word for p in preflight.pronunciation_flags[:8]]
        console.print(f"    {', '.join(words)}")
        if len(preflight.pronunciation_flags) > 8:
            console.print(f"    ... and {len(preflight.pronunciation_flags) - 8} more")
        console.print()

    # Warnings
    if preflight.warnings:
        console.print("  [yellow]Warnings:[/yellow]")
        for w in preflight.warnings:
            console.print(f"    [yellow]! {w}[/yellow]")
        console.print()


def _display_toggles(toggles: FormatToggles) -> None:
    """Display current toggle settings."""
    display_items = toggles_to_display(toggles)

    console.print("  [bold]Current toggles:[/bold]")
    for i, item in enumerate(display_items, 1):
        val = item["value"]
        if isinstance(val, bool):
            marker = "[green]X[/green]" if val else " "
            console.print(f"    {i:2d}. [{marker}] {item['display_name']}")
        else:
            console.print(f"    {i:2d}. [ ] {item['display_name']}: {val}")
    console.print()


def _prompt_toggle_review(toggles: FormatToggles) -> FormatToggles | None:
    """Interactive toggle review. Returns updated toggles or None to abort."""
    while True:
        _display_toggles(toggles)

        choice = Prompt.ask(
            "  Accept?",
            choices=["y", "n", "edit"],
            default="y",
        )

        if choice == "y":
            return toggles
        elif choice == "n":
            console.print("  Aborted.")
            return None
        elif choice == "edit":
            toggles = _edit_toggles(toggles)


def _edit_toggles(toggles: FormatToggles) -> FormatToggles:
    """Interactive toggle editing."""
    display_items = toggles_to_display(toggles)

    while True:
        num_str = Prompt.ask("  Toggle number (or 'done')", default="done")
        if num_str.lower() == "done":
            break

        try:
            num = int(num_str)
            if num < 1 or num > len(display_items):
                console.print(f"    Invalid number. Enter 1-{len(display_items)}.")
                continue
        except ValueError:
            console.print("    Enter a number or 'done'.")
            continue

        item = display_items[num - 1]
        name = item["name"]
        current = item["value"]

        if item["type"] == bool:
            # Flip boolean
            new_val = not current
            setattr(toggles, name, new_val)
            state = "ON" if new_val else "OFF"
            old_state = "ON" if current else "OFF"
            console.print(f"    {item['display_name']}: {old_state} -> {state}")
        elif "choices" in item:
            # Choice selection
            choices_str = "/".join(str(c) for c in item["choices"])
            new_val = Prompt.ask(
                f"    {item['display_name']} ({choices_str})",
                default=str(current),
            )
            # Coerce to correct type
            if item["type"] == int:
                new_val = int(new_val)
            elif item["type"] == float:
                new_val = float(new_val)

            # Handle enum conversion
            if name == "narrator_style":
                new_val = NarratorStyle(new_val)
            elif name == "quoted_text_style":
                new_val = QuotedTextStyle(new_val)
            elif name == "margins":
                new_val = MarginPreset(new_val)

            setattr(toggles, name, new_val)
            console.print(f"    {item['display_name']}: {current} -> {new_val}")
        else:
            new_val = Prompt.ask(
                f"    {item['display_name']}",
                default=str(current),
            )
            if item["type"] == float:
                new_val = float(new_val)
            elif item["type"] == int:
                new_val = int(new_val)
            setattr(toggles, name, new_val)
            console.print(f"    {item['display_name']}: {current} -> {new_val}")

    return toggles


# ---------------------------------------------------------------------------
# Diagnostics display
# ---------------------------------------------------------------------------


def _display_diagnostic(report) -> None:
    """Display diagnostic report."""
    console.print()
    console.print(Panel.fit(
        "[bold]Diagnostic Report[/bold]",
        border_style="yellow",
    ))
    console.print()

    if report.summary:
        console.print(f"  [bold]Summary:[/bold] {report.summary}")
        console.print()

    if report.misclassified_lines:
        console.print(f"  [yellow]Misclassified lines: {len(report.misclassified_lines)}[/yellow]")
        for entry in report.misclassified_lines[:10]:
            console.print(f"    Line {entry.line_number}: {entry.issue}")
            console.print(f"      Was: {entry.assigned_type} -> Should be: {entry.suggestion}")
            text_preview = entry.original_text[:60]
            console.print(f"      Text: {text_preview}")
        if len(report.misclassified_lines) > 10:
            console.print(f"    ... and {len(report.misclassified_lines) - 10} more")
        console.print()

    if report.missed_characters:
        console.print(f"  [yellow]Missed characters:[/yellow] {', '.join(report.missed_characters)}")
        console.print()

    if report.unhandled_patterns:
        console.print("  [yellow]Unhandled patterns:[/yellow]")
        for p in report.unhandled_patterns:
            console.print(f"    - {p}")
        console.print()

    if not any([
        report.misclassified_lines,
        report.missed_characters,
        report.missed_stage_directions,
        report.missed_sound_cues,
        report.unstripped_metadata,
        report.unhandled_patterns,
    ]):
        console.print("  [green]No issues found.[/green]")
        console.print()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Resolve output path
    if args.output:
        output_path = args.output
    else:
        base = os.path.splitext(os.path.basename(args.script_file))[0]
        output_dir = os.path.dirname(os.path.abspath(args.script_file))
        output_path = os.path.join(output_dir, f"{base}_formatted.pdf")

    # Step 1: Read and normalize input
    try:
        raw_text, file_type = extract_text(args.script_file)
    except (FileNotFoundError, ValueError, ImportError) as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)

    normalized = normalize_text(raw_text)
    filename = os.path.basename(args.script_file)
    line_count = normalized.count("\n") + 1
    console.print(f"  Read {filename} ({line_count} lines, .{file_type})")

    # Step 2: Preflight or defaults
    chosen_backend = resolve_backend(args.backend)
    preflight: PreflightResult | None = None
    if not args.no_preflight:
        console.print(f"  Analyzing script (backend: {chosen_backend})...", end="")
        try:
            preflight = run_preflight(
                chosen_backend,
                normalized,
                filename,
                api_key=args.api_key,
            )
            console.print(" [green]done[/green]")
        except PreflightError as e:
            console.print(f" [red]failed[/red]")
            console.print(f"  [red]{e}[/red]")
            console.print("  Falling back to defaults.")
    else:
        console.print("  Preflight skipped (--no-preflight)")

    # Build a default preflight if we don't have one
    if preflight is None:
        archetype = Archetype(args.archetype) if args.archetype else Archetype.MULTI_VOICE_DRAMA
        preflight = PreflightResult(
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

    # Assign colors
    color_map = assign_colors(preflight.characters, preflight.has_narrator)

    # Display preflight results
    _display_preflight(preflight, color_map)

    # Step 3: Resolve toggles
    cli_overrides = _collect_cli_overrides(args)
    toggles = resolve_toggles(
        preflight.archetype,
        preflight.suggested_toggles,
        cli_overrides,
    )

    # Step 4: Interactive toggle review
    if not args.non_interactive and sys.stdin.isatty():
        result = _prompt_toggle_review(toggles)
        if result is None:
            sys.exit(0)
        toggles = result
    else:
        _display_toggles(toggles)

    # Step 4b: Pronunciation guide (optional, extra API call)
    pronunciation_guide: dict[str, str] = {}

    # AUTO logic: if preflight detected pronunciation flags and user didn't
    # explicitly set the toggle, auto-enable it
    cli_pronunciation = getattr(args, "pronunciation_guide", None)
    if (
        cli_pronunciation is None
        and preflight.pronunciation_flags
        and not args.no_preflight
    ):
        toggles.pronunciation_guide = True

    if toggles.pronunciation_guide and preflight.pronunciation_flags:
        words = [p.word for p in preflight.pronunciation_flags]
        console.print("  Generating pronunciation guide...", end="")
        pronunciation_guide = run_pronunciation(
            chosen_backend,
            words,
            script_context=f"{preflight.archetype.value.replace('_', ' ')} script",
            api_key=args.api_key,
        )
        if pronunciation_guide:
            console.print(f" [green]done[/green] ({len(pronunciation_guide)} words)")
        else:
            console.print(" [yellow]no results[/yellow]")

    # Step 5: Format
    console.print("  Formatting...", end="")
    blocks = format_script(
        normalized, preflight, toggles, filename,
        pronunciation_guide=pronunciation_guide or None,
    )
    console.print(f" [green]done[/green] ({len(blocks)} blocks)")

    # Step 5b: Terminal preview (optional)
    if args.preview:
        console.print()
        render_preview(blocks, toggles, console)
        console.print()

        # Ask whether to also generate PDF
        generate = True
        if not args.non_interactive and sys.stdin.isatty():
            generate = Confirm.ask("  Generate PDF?", default=False)

        if not generate:
            console.print("  Skipped PDF generation.")
            console.print()
            sys.exit(0)

    # Step 6: Generate PDF
    console.print(f"  Generating PDF...", end="")
    try:
        output_path = generate_pdf(blocks, output_path, toggles)
        console.print(f" [green]done[/green]")
        console.print(f"\n  [bold green]Output:[/bold green] {output_path}")
    except Exception as e:
        console.print(f" [red]failed[/red]")
        console.print(f"  [red]PDF generation error: {e}[/red]")
        sys.exit(1)

    # Step 7: Diagnostics (optional)
    if args.diagnose:
        console.print("\n  Running diagnostic review...")

        # Save debug JSON
        debug_path = output_path.replace(".pdf", "_debug.json")
        debug_data = {
            "preflight": {
                "archetype": preflight.archetype.value,
                "characters": [
                    {"name": c.name, "line_count": c.line_count, "color": color_map.get(c.name, "")}
                    for c in preflight.characters
                ],
                "has_narrator": preflight.has_narrator,
                "sections": [
                    {"title": s.title, "start_line": s.start_line, "end_line": s.end_line}
                    for s in preflight.sections
                ],
                "metadata_blocks": [
                    {"type": m.type, "start_line": m.start_line, "end_line": m.end_line}
                    for m in preflight.metadata_blocks
                ],
                "warnings": preflight.warnings,
            },
            "toggles": {f.name: getattr(toggles, f.name) for f in toggles.__dataclass_fields__.values()},
            "blocks": [
                {
                    "type": b.block_type.value,
                    "text": b.text[:100],
                    "source_line": b.source_line,
                    "match_pattern": b.match_pattern,
                }
                for b in blocks
            ],
        }
        # Convert enum values for JSON serialization
        for key, val in debug_data["toggles"].items():
            if hasattr(val, "value"):
                debug_data["toggles"][key] = val.value

        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(debug_data, f, indent=2, ensure_ascii=False)
        console.print(f"  Debug data saved: {debug_path}")

        # Run diagnostic API call
        report = run_diagnostic(
            chosen_backend,
            normalized,
            preflight,
            blocks,
            api_key=args.api_key,
        )
        _display_diagnostic(report)

    console.print()


def _collect_cli_overrides(args: argparse.Namespace) -> dict:
    """Collect explicitly-set CLI toggle values (exclude None/unset)."""
    toggle_fields = [
        "color_characters", "narrator_style", "section_breaks",
        "stage_directions", "sound_cues", "source_labels",
        "quoted_text_style", "strip_metadata", "title_page",
        "character_legend", "breathing_marks", "pause_notation",
        "pronunciation_guide", "voice_batch", "cold_read_breaks",
        "font_size", "line_spacing", "margins",
    ]
    overrides = {}
    for field in toggle_fields:
        val = getattr(args, field, None)
        if val is not None:
            overrides[field] = val
    return overrides


if __name__ == "__main__":
    main()
