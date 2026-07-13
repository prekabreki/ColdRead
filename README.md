# ColdRead

> A desktop app that turns raw scripts into clean, color-coded PDFs built for voice-over cold reads.

You've got a script to record, and it fights you the whole way. It's one flat wall of text: no color telling you who's speaking, nowhere obvious to breathe, stage directions buried in the dialogue, a proper noun or two you'll fumble on the first take. ColdRead turns that raw file (Markdown, plain text, PDF, or Word) into a PDF built for reading out loud. Claude reads the script once to map its structure and flag the hard words, then hands back a plan; the layout itself is plain Python, so your text is never rewritten, only arranged. What comes back is color-coded by speaker, spaced wide, broken at natural breathing points, and still legible when you print it in black and white. It's the busywork you'd otherwise do by hand before every session, done in one pass.

You mostly drive it from a desktop window: drop a script in, flip the toggles you want, and watch the formatted PDF redraw as you go. There's a command line too, for scripting and headless machines.

## Features

- **Live desktop GUI** — the main way to use it: an inline PDF preview that redraws as you flip toggles, drag-and-drop input, saved toggle presets, and intro/outro textboxes that wrap the formatted output.
- **Five script archetypes** — document archive, multi-voice drama, single narrator, continuous prose, mixed media. The analysis step picks one and seeds sensible toggle defaults for it.
- **Cold-read formatting** — color-coded speakers (the palette varies in lightness, so it survives grayscale printing), wide leading, breath-group line breaks, optional pronunciation hints for tricky names.
- **Runs without API credit** — do the analysis through the Anthropic API, or your local Claude Code subscription, or skip it entirely with archetype defaults.
- **Optional diagnostics** — a second pass flags lines the formatter likely misclassified.

## Requirements

- **Python 3.10 or newer**, including Tk (the standard library's GUI toolkit). The python.org installers for Windows and macOS include it; on Linux you may need your distro's `python3-tk` package.
- **For the AI analysis step:** an `ANTHROPIC_API_KEY`, *or* the [Claude Code](https://claude.com/claude-code) CLI signed in. Neither is needed if you run with `--no-preflight`.

## Install

You only do this once. Follow it line by line, even if you're not a Python person.

**1. Check your Python version.**

```bash
python --version        # on some systems the command is python3
```

It should print 3.10 or higher. If it doesn't, or the command isn't found, install Python from [python.org/downloads](https://www.python.org/downloads/) — on Windows, tick **"Add Python to PATH"** during setup.

**2. Get ColdRead.**

```bash
git clone https://github.com/prekabreki/ColdRead.git
cd ColdRead
```

No git? On the GitHub page, use the green **Code ▸ Download ZIP** button, unzip it, and open a terminal in the unzipped folder.

**3. (Recommended) Create a virtual environment**, so ColdRead's packages stay out of your system Python:

```bash
python -m venv .venv
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows (PowerShell or cmd)
```

**4. Install ColdRead.**

```bash
pip install -e .
```

Done. You now have the `coldread-gui` and `coldread` commands.

## Running it (the GUI)

**ColdRead is meant to be used from its desktop app** — that's where it earns its keep. You drop a script onto the window, flip toggles, and the PDF redraws live, so you see exactly what you'll get before you export it.

Open it either way:

- **Double-click `launch.bat`** (Windows) or **run `./launch.sh`** (Linux/macOS) from the ColdRead folder. This is the no-terminal path: the launcher opens a built bundle if one exists, otherwise your install, and if a dependency is missing it prints exactly what to run instead of failing silently.
- Or, from a terminal with your environment active, run `coldread-gui`.

Then drop in a script (`.md`, `.txt`, `.pdf`, or `.docx`), review the toggles ColdRead suggests, and export the PDF. Point it at one of the bundled files in `samples/` first to see what it does.

### A standalone app with no Python (optional)

Build a single-file bundle once and hand that around instead:

```bash
pip install pyinstaller
pyinstaller ColdRead.spec
# Windows → dist/ColdRead.exe    Linux/macOS → dist/ColdRead
```

## Command line (optional)

Same engine, no window — handy for scripting or a headless box:

```bash
coldread script.md
coldread script.md --diagnose --preview
coldread samples/multi_voice_drama_sample.md --no-preflight --archetype multi_voice_drama   # fully offline, no key
```

Run the test suite with `python -m pytest tests/` (no API key needed).

## How it works

Extract the text, ask Claude for a structural read of the script (returned as JSON only), resolve the toggles, format deterministically in Python, then render the PDF with ReportLab. Claude classifies; it never rewrites. That split is deliberate: the same script and toggles always produce the same PDF, and your words come out exactly as you wrote them.

## Configuration

- **Analysis backend.** In the GUI, choose **API** or **Claude Code** in the backend selector; on the CLI, `--backend api` / `--backend claude-code`. The API backend needs `ANTHROPIC_API_KEY`. The Claude Code backend shells out to your local `claude` CLI and uses your Claude.ai subscription, so it costs no API credit. The default picks the API if a key is set, otherwise the CLI if it's on your PATH. `--no-preflight` skips analysis and formats from archetype defaults.
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
