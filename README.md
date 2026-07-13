# ColdRead

> A desktop app that turns raw scripts into clean, color-coded PDFs built for voice-over cold reads.

You've got a script to record, and it fights you the whole way. It's one flat wall of text: no color telling you who's speaking, nowhere obvious to breathe, stage directions buried in the dialogue, a proper noun or two you'll fumble on the first take. ColdRead turns that raw file (Markdown, plain text, PDF, or Word) into a PDF built for reading out loud. Claude reads the script once to map its structure and flag the hard words, then hands back a plan; the layout itself is plain Python, so your text is never rewritten, only arranged. What comes back is color-coded by speaker, spaced wide, broken at natural breathing points, and still legible when you print it in black and white. It's the busywork you'd otherwise do by hand before every session, done in one pass.

You mostly drive it from a desktop window: drop a script in, flip the toggles you want, and watch the formatted PDF redraw as you go. There's a command line too, for scripting and headless machines.

## Download and run

The easiest way, and the way it's meant to be used — no Python, no terminal:

1. Open the [latest release](https://github.com/prekabreki/ColdRead/releases/latest).
2. Download the file for your system:
   - **Windows** — `ColdRead-windows-x86_64.exe`
   - **macOS** — `ColdRead-macos-arm64` (first launch: right-click ▸ **Open** to clear the "unidentified developer" prompt)
   - **Linux** — `ColdRead-linux-x86_64` (mark it executable first: `chmod +x ColdRead-linux-x86_64`)
3. Run it. The GUI opens.

Have Python and prefer a one-liner? Install from PyPI instead:

```bash
pipx install coldread       # or: pip install coldread
coldread-gui                # start the app
```

## Using the GUI

Drop a script onto the window (`.md`, `.txt`, `.pdf`, or `.docx`), review the toggles ColdRead suggests, watch the preview redraw, and export the PDF. Point it at one of the bundled sample scripts first to see what it does; they ship with the install, and `coldread --list-samples` prints their paths. To auto-detect the script type and flag tricky proper nouns, ColdRead runs a quick AI analysis pass (see [Configuration](#configuration)); you can also skip it and format straight from archetype defaults.

## Features

- **Live desktop GUI** — the main way to use it: an inline PDF preview that redraws as you flip toggles, drag-and-drop input, saved toggle presets, and intro/outro textboxes that wrap the formatted output.
- **Five script archetypes** — document archive, multi-voice drama, single narrator, continuous prose, mixed media. The analysis step picks one and seeds sensible toggle defaults for it.
- **Cold-read formatting** — color-coded speakers (the palette varies in lightness, so it survives grayscale printing), wide leading, breath-group line breaks, optional pronunciation hints for tricky names.
- **Runs without API credit** — do the analysis through the Anthropic API, or your local Claude Code subscription, or skip it entirely with archetype defaults.
- **Optional diagnostics** — a second pass flags lines the formatter likely misclassified.

## Run from source

For development, or to build your own bundle. You set this up once.

**1. Check your Python** (3.10+, with Tk):

```bash
python --version        # some systems: python3
```

If it's missing or older, install from [python.org/downloads](https://www.python.org/downloads/) — on Windows tick **"Add Python to PATH"**; on Linux you may also need your distro's `python3-tk` package.

**2. Get the code and install it:**

```bash
git clone https://github.com/prekabreki/ColdRead.git
cd ColdRead
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

That gives you the `coldread-gui` and `coldread` commands. Launch the GUI with `coldread-gui`, or double-click `launch.bat` / run `./launch.sh` — the launcher finds your install (or a built bundle) and tells you what's missing instead of failing silently.

**Build a standalone bundle** like the ones on the releases page:

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
coldread --list-samples                                   # find the bundled sample scripts
coldread vo_format/samples/multi_voice_drama_sample.md --no-preflight --archetype multi_voice_drama   # offline, no key
```

Run the test suite with `python -m pytest tests/` (no API key needed).

## How it works

Extract the text, ask Claude for a structural read of the script (returned as JSON only), resolve the toggles, format deterministically in Python, then render the PDF with ReportLab. Claude classifies; it never rewrites. That split is deliberate: the same script and toggles always produce the same PDF, and your words come out exactly as you wrote them.

## Configuration

- **Analysis backend.** In the GUI, choose **API** or **Claude Code** in the backend selector; on the CLI, `--backend api` / `--backend claude-code`. The API backend needs `ANTHROPIC_API_KEY`. The Claude Code backend shells out to your local `claude` CLI and uses your Claude.ai subscription, so it costs no API credit. The default picks the API if a key is set, otherwise the CLI if it's on your PATH. `--no-preflight` skips analysis and formats from archetype defaults.
- **Fonts.** PDFs use Courier New when its TrueType files are installed, then fall back to Liberation Mono (metric-compatible) and finally ReportLab's built-in Courier. The app scans the native per-OS font directories plus anything in `VO_FONT_DIRS` (an `os.pathsep`-separated list). On Linux/macOS you can install Courier New by copying `cour.ttf` / `courbd.ttf` / `couri.ttf` / `courbi.ttf` into `~/.local/share/fonts` and running `fc-cache -f`.

## Layout

```
vo_format/           the Python package (parser, preflight, formatter, pdf_writer, gui, …)
vo_format/samples/   sample inputs, one per archetype (bundled with the install)
tests/               pytest unit tests (no API key required)
```

See [`CLAUDE.md`](CLAUDE.md) for module responsibilities, the archetype table, and the design invariants.

## Issues

Bugs and feature ideas are welcome on the GitHub issue tracker.

## License

MIT — see [`LICENSE`](LICENSE). One dependency to note: PyMuPDF (`pymupdf`) is licensed AGPL-3.0 (or a paid commercial license). ColdRead only depends on it for PDF text extraction and doesn't modify it, but if you redistribute your own build, check PyMuPDF's terms for your case.
