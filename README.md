# ColdRead

> Formats raw scripts into clean, color-coded PDFs built for voice-over cold reads.

You've got a script to record, and it fights you the whole way. It's one flat wall of text: no color telling you who's speaking, nowhere obvious to breathe, stage directions buried in the dialogue, a proper noun or two you'll fumble on the first take. ColdRead turns that raw file (Markdown, plain text, PDF, or Word) into a PDF built for reading out loud. Claude reads the script once to map its structure and flag the hard words, then hands back a plan; the layout itself is plain Python, so your text is never rewritten, only arranged. What comes back is color-coded by speaker, spaced wide, broken at natural breathing points, and still legible when you print it in black and white. It's the busywork you'd otherwise do by hand before every session, done in one pass.

## Features

- **Five script archetypes** — document archive, multi-voice drama, single narrator, continuous prose, mixed media. The analysis step picks one and seeds sensible toggle defaults for it.
- **Cold-read formatting** — color-coded speakers (palette varies in lightness, so it survives grayscale printing), wide leading, breath-group line breaks, optional pronunciation hints for tricky names.
- **Runs without API credit** — do the analysis through the Anthropic API, or your local Claude Code subscription, or skip it entirely with `--no-preflight` and archetype defaults.
- **Toggle presets** — save named snapshots of your formatting options; each archetype also has its own defaults.
- **Desktop GUI** — live inline PDF preview as you flip toggles, drag-and-drop input, and intro/outro textboxes that wrap the formatted output.
- **Optional diagnostics** — a second pass flags lines the formatter likely misclassified.

## Requirements

- Python 3.10+ to run from source (or just use a prebuilt GUI bundle).
- For the analysis step: an `ANTHROPIC_API_KEY`, **or** the [Claude Code](https://claude.com/claude-code) CLI signed in. Neither is needed if you run with `--no-preflight`.

## Install

```bash
pip install -e .
```

This puts two commands on your path: `coldread` (CLI) and `coldread-gui` (desktop app).

## Usage

CLI:

```bash
coldread path/to/script.md
coldread script.md --no-preflight --archetype multi_voice_drama
coldread script.md --diagnose --preview
```

Try it on a bundled sample, no API key needed:

```bash
coldread samples/multi_voice_drama_sample.md --no-preflight --archetype multi_voice_drama
```

GUI:

```bash
coldread-gui
```

Or use the launcher, which runs a built bundle if one exists and otherwise falls back to the module:

```bash
./launch.sh      # Linux / macOS
launch.bat       # Windows
```

Build a standalone GUI bundle (PyInstaller, cross-platform):

```bash
pyinstaller ColdRead.spec
# Windows → dist/ColdRead.exe    Linux/macOS → dist/ColdRead
```

Run the tests (no API key required):

```bash
python -m pytest tests/
```

## How it works

Extract the text, ask Claude for a structural read of the script (returned as JSON only), resolve the toggles, format deterministically in Python, then render the PDF with ReportLab. Claude classifies; it never rewrites. That split is deliberate: the same script and toggles always produce the same PDF, and your words come out exactly as you wrote them.

## Configuration

- **Analysis backend.** `--backend api` uses the Anthropic API directly (needs `ANTHROPIC_API_KEY`). `--backend claude-code` shells out to the local `claude` CLI and uses your Claude.ai subscription. The default picks the API if a key is set, otherwise the CLI if it's on your PATH. `--no-preflight` skips analysis and formats from archetype defaults.
- **Fonts.** PDFs use Courier New when its TrueType files are installed, then fall back to Liberation Mono (metric-compatible) and finally ReportLab's built-in Courier. The app scans the native per-OS font directories plus anything in `VO_FONT_DIRS` (an `os.pathsep`-separated list). On Linux/macOS you can install Courier New by copying `cour.ttf` / `courbd.ttf` / `couri.ttf` / `courbi.ttf` into `~/.local/share/fonts` and running `fc-cache -f`.

## Layout

```
vo_format/   the Python package (parser, preflight, formatter, pdf_writer, gui, …)
tests/       pytest unit tests (no API key required)
samples/     clean sample inputs, one per archetype
```

See [`CLAUDE.md`](CLAUDE.md) for module responsibilities, the archetype table, and the design invariants.

## Issues

Bugs and feature ideas are welcome on the GitHub issue tracker.

## License

MIT — see [`LICENSE`](LICENSE). One dependency to note: PyMuPDF (`pymupdf`) is licensed AGPL-3.0 (or a paid commercial license). ColdRead only depends on it for PDF text extraction and doesn't modify it, but if you redistribute your own build, check PyMuPDF's terms for your case.
