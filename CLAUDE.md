# Project Instructions for AI Agents

This file provides context for AI coding agents working on ColdRead.

## Project

**ColdRead** — a Python tool (CLI + CustomTkinter GUI) that turns raw scripts (`.md`, `.txt`, `.pdf`, `.docx`) into cold-read-optimized PDFs for voice-over recording. Code lives in [`vo_format/`](vo_format/) (the import package keeps its original name); [`samples/`](samples/) holds clean sample inputs, one per archetype.

Issue tracking is via GitHub Issues.

## Common commands

```bash
# Install in editable mode (Python 3.10+)
pip install -e .

# Run the CLI
python -m vo_format.cli <script> [flags]
coldread <script> [flags]              # after install

# Run the GUI
python -m vo_format.gui_main
coldread-gui                           # after install

# Run tests (pytest, no API key required)
python -m pytest tests/

# Build the single-file GUI bundle (cross-platform via PyInstaller)
pyinstaller ColdRead.spec
# Windows → dist/ColdRead.exe   ·   Linux/macOS → dist/ColdRead
```

There is no linter config or CI — don't invent commands for them. For integration validation beyond the unit tests, run the CLI against the sample scripts in `samples/`.

### Useful CLI flags

- `--no-preflight` — skip the Claude API call (uses archetype defaults; combine with `--archetype`)
- `--non-interactive` — skip the toggle-review prompt (useful when scripting)
- `--preview` — render a Rich terminal preview instead of / before the PDF
- `--diagnose` — after formatting, do a second API call that flags misclassified lines; also dumps `<output>_debug.json`
- Toggle flags follow `--flag` / `--no-flag` pairs (see `vo_format/cli.py:92-117`)

### API key

The preflight, pronunciation, and diagnostic calls all need `ANTHROPIC_API_KEY` in env (or `--api-key`). No file-based key fallback — don't add one. The model is pinned to `claude-sonnet-4-5-20250929` (see `preflight.py`); update there if migrating.

## Architecture

The pipeline is strictly one-direction and deterministic after the preflight step. Claude analyzes; Python formats. **Original script text is never sent back through the API for rewriting** — this is a load-bearing design invariant.

```
extract_text → normalize_text → run_preflight (Claude API, JSON only)
  → resolve_toggles (archetype defaults ← preflight suggestions ← CLI/GUI overrides)
  → [optional] run_pronunciation (2nd API call)
  → format_script → list[FormattedBlock]
  → generate_pdf (ReportLab)
  → [optional] run_diagnostic (3rd API call, quality review)
```

### Module responsibilities (under `vo_format/`)

- `models.py` — single source of truth for enums (`Archetype`, `BlockType`, `LineType`, `NarratorStyle`, `QuotedTextStyle`, `MarginPreset`), dataclasses (`PreflightResult`, `FormatToggles`, `FormattedBlock`, `DiagnosticReport`). Touch this first when adding new toggle/block types.
- `parser.py` — format-specific extraction (`pymupdf` for PDF, `python-docx` for DOCX, encoding fallback for text) and `normalize_text` (line endings, BOM, base64 image stripping, blank-line collapsing).
- `preflight.py` — all Claude API interaction. Contains three system prompts (preflight, pronunciation, diagnostic) and the JSON-extraction helper that tolerates markdown fences. Long scripts (>200K chars) are sampled head/middle/tail before sending.
- `toggles.py` — `TOGGLE_DEFINITIONS` drives both the CLI argparse flags and the interactive toggle editor. `ARCHETYPE_DEFAULTS` maps each archetype to its default toggle overrides. `resolve_toggles` enforces the priority order: global defaults → archetype defaults → preflight suggestions → CLI/GUI overrides.
- `formatter.py` — deterministic engine. Regex-based line classification (see the `RE_*` patterns) dispatched per-archetype. Produces a flat `list[FormattedBlock]`. This is the biggest file; most formatting bugs live here.
- `cold_read.py` — optional post-pass that rewraps dialogue/narration at natural breath-group boundaries. Must stay in sync with `pdf_writer.py`'s `leftIndent` values (see `_STYLE_INDENT`).
- `pdf_writer.py` — ReportLab rendering. Registers Windows Courier New TTFs if present (path: `%WINDIR%/Fonts`) and falls back to Type-1 Courier otherwise. `BlockType` values map 1:1 to `ParagraphStyle`s here.
- `colors.py` — 8-color palette (`PALETTE`) and `assign_colors` (narrator always black, others assigned by line-count rank).
- `preview.py` — Rich-based terminal renderer.
- `cli.py` / `gui_main.py` + `gui.py` — entry points. GUI adds live preview (renders PDF pages via PyMuPDF to PIL images), drag-and-drop (`tkinterdnd2`), tooltips (`CTkToolTip`), and a `PreflightCache` (SHA-256 of normalized text) so re-running on the same file is free. The GUI also has an **intro/outro** section (textboxes that inject `BlockType.INTRO` / `BlockType.OUTRO` blocks around the formatter output — see `_wrap_with_intro_outro` in `gui.py`). Intro is inserted *after* any title page / character legend frontmatter so the cover stays first; outro is appended at the very end. Intro/outro are GUI-only (no CLI flags, no preflight involvement).
- `presets.py` — named toggle snapshots saved as JSON to `~/.vo-formatter/presets/` (override via `VO_FORMATTER_PRESETS_DIR`). `BUILTIN_PRESETS` cannot be deleted.
- `cache.py` — in-memory `PreflightCache`, used by the GUI only.

### Adding a new toggle

1. Add the field to `FormatToggles` in `models.py` with a sane default.
2. Add an entry to `TOGGLE_DEFINITIONS` in `toggles.py` (this auto-wires the CLI flag and interactive editor).
3. If archetype-specific defaults differ, update `ARCHETYPE_DEFAULTS`.
4. Add a flag pair in `cli.py` (`_add_bool_flag` or a typed `add_argument`) and include the field name in `_collect_cli_overrides`'s `toggle_fields`.
5. Consume the toggle in `formatter.py` and/or `pdf_writer.py`.
6. If it needs AI input, extend `preflight.py` and wire the result through `cli.py` / `gui.py`.

### Script archetypes

The preflight classifies every script into one of five archetypes (`models.Archetype`), which selects a default toggle profile:

| Archetype | Sample fixture | Defaults emphasize |
|---|---|---|
| `document_archive` | `samples/document_archive_sample.md` | source labels on, character colors off |
| `multi_voice_drama` | `samples/multi_voice_drama_sample.md` | character colors + legend on |
| `single_narrator` | `samples/single_narrator_sample.md` | `quoted_text_style=indent+italic` |
| `continuous_prose` | `samples/continuous_prose_sample.md` | breathing marks on, no speaker labels |
| `mixed_media` | `samples/mixed_media_sample.md` | source labels + character colors on |

Use these samples when you touch `formatter.py` or archetype defaults.

## Key design constraints

- **No content rewriting.** Claude only returns structural JSON. If you're tempted to add a prompt that returns rewritten text, stop.
- **One preflight API call per script** (plus optional pronunciation + diagnostic). Don't add per-line or per-block calls.
- **Bold `**NAME:**`** (markdown-bold character attribution) and plain-caps `NAME:` must both parse as character lines — this is what `RE_BOLD_CHARACTER` / `RE_PLAIN_CHARACTER` in `formatter.py` exist for.
- **Editorial/production notes** ("Add after Document Archive B-047 …") must be detected as metadata and stripped when `strip_metadata` is on.
- **Grayscale legibility** — `PALETTE` colors vary in lightness, not just hue, so printed B&W still distinguishes speakers. Preserve this when extending the palette.
- **Intro/outro injection happens post-formatter.** The GUI builds intro/outro blocks from its textboxes and splices them into the block list after `format_script` returns — they never pass through the classifier or preflight. If you move this logic, keep it post-formatter; don't concatenate intro/outro text into the script before extraction.
