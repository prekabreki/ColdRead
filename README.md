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

# Or, if you plan to run the test suite or the linters:
pip install -e ".[dev]"
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

## Teleprompter read-view

`coldread-readview` turns an already-formatted ColdRead PDF into a single
self-contained HTML page that auto-scrolls, for reading aloud off a tablet.

```bash
coldread-readview "path/to/My Script - formatted.pdf"
# writes "path/to/My Script - formatted - readview.html"
```

The output name keeps the PDF's full stem rather than just swapping the
extension, so a `formatted` cut and a `batched` cut of the same title each get
their own HTML file instead of overwriting one another.

It reads the PDF's embedded text layer, so speaker colors, bold and italic, the
size hierarchy, indentation, and the cold-read breath-group line breaks all
carry over exactly. Pages do not: the result is one continuous scroll with no
page seams.

The output has no external references of any kind — all CSS, JS, and the
keep-awake video are inlined — so it works with the network off.

**Controls:** hold a finger on the text to freeze it, drag to reposition, lift to
resume. A quick flick glides on and settles instead of stopping dead; putting a
finger back down stops it where it is. The stacked `−`/`+` pair sitting just above
the HUD at the bottom right steps the speed in words per minute; hold either one
and it ramps rather than stepping once. `A−`/`A+` set type size. Space, arrow keys and
Page Up/Down do the same from a keyboard, which is also how a Bluetooth foot pedal
reaches it.

The HUD shows how far through the script you are, as a percentage beside the wpm
and as a fill along its top edge, and how long is left at the current speed. The
countdown comes from the same figure that drives the scroll, so it moves when you
change the speed or the type size, and it is there while paused — "will this fit
the time I have" is a question asked before a take, not during one.

**Resume mark:** press and hold a word for half a second to mark where you
stopped. It highlights amber, survives closing the page, and reopening the script
scrolls straight back to it with a little run-up above. Press it again to clear
it, or hold a different word to move it — there is only ever one. Publishing a new
draft starts clean, since the mark is stored against the filename's version.

Re-running skips any page whose HTML is already strictly newer than its PDF;
pass `--force` to re-render anyway.

### The library

One read-view per PDF is enough for one script. For a session's worth of them —
and for a tablet that won't open a local file — there has to be something to
navigate between them.

That something is a directory of read-views plus a generated `index.html`, served
over HTTP by whatever you like. In practice it means a Raspberry Pi on a shelf
somewhere, quietly serving a handful of scripts to a tablet. It is an odd little
thing to have built, which is why this half is optional and off by default. It's
documented because it works well, in case anyone else wants one.

```bash
python vo_format/readview/library.py /path/to/directory
# writes /path/to/directory/index.html
```

The index lists every `.html` in that directory except `index.html` itself. Each
row shows the title, the word count, and the date the read-view was derived — the
length because "does this fit the time I have" is what the page gets asked, and
the date because the directory holds copies, so a stale one should be visible
rather than worked out.

Scripts group into collapsible sections by the `Channel — Title` prefix on the
filename. A file without one still gets a row, filed under `Unfiled` — nothing is
dropped from the only navigation a tablet has. Sections start closed and remember
being opened. To give a channel a display name, or pin the order they appear in,
drop a `channels.json` beside the read-views:

```json
{ "order": ["Fiction", "Interviews"],
  "labels": { "Fiction": "The Fiction Channel" } }
```

Channels missing from `order` follow alphabetically and show their prefix as-is.

**Marking scripts done:** swipe a row left past about 60px, or tap its `✓` if
there's no touchscreen. The row dims, its `✓` lights up, and it drops to the
bottom of its section, with a tally on the section's summary line. Swipe or tap
again to undo. Read state is keyed on the filename, which carries the draft
version, so publishing a new draft brings a script back unread rather than
inheriting a tick it didn't earn. The `⟳` top right refetches the page.

`coldread-readview --library index.html` adds a `← Library` button to the
read-view's HUD, pointing back at that index. Off by default: a read-view
converted on its own has no library to return to, and a dead button is worse than
no button.

The generator imports nothing outside the standard library and nothing from
ColdRead, so it can run on the serving box without installing anything there:

```bash
ssh box "python3 - /path/to/directory" < vo_format/readview/library.py
```

That is the invocation it's built for. Generating the index where the files
actually are matters if you ever publish one channel at a time — an index built
somewhere else would quietly omit whatever that run didn't stage.

**Two things the pages need from however you serve them.** They want **HTTPS**,
because keeping a tablet awake needs `navigator.wakeLock` and browsers only
expose that in a secure context; over plain HTTP an iPad dimmed and slept inside
two minutes, mid-read. And they want a **real origin**, because Safari blocks
`localStorage` on `file://`, which is where the speed, type size, scroll position
and resume mark are kept. Any static HTTP server will do for both. If the box has
no public DNS name to get a certificate for, Tailscale is one way to give it one.

**Shared read state (optional):** resume marks, which scripts are finished, and
the per-script speed live in the browser's local storage, which means they live
in one browser. `coldread-state` is a small service that keeps them in a single
JSON file instead, so a mark set on the phone is there on the desktop. It is
entirely optional and does nothing unless a page is pointed at it: without it the
read-view behaves exactly as described above, keeps everything locally, and still
works with the network off.

```bash
coldread-state --state-file /path/to/state.json \
               --token-file /path/to/state.token
# --check validates the paths and the token, then exits without serving
```

It imports nothing outside the Python standard library, so it can run on whatever
box already serves the pages — a Raspberry Pi, or anything else cheap and always
on, with nothing installed on it.

It wants to sit on the same origin as the library. Put it behind the same web
server at a path like `/state`, then point the read-view at that path with
`coldread-readview --sync /state`. The page's request is then same-origin, which
is what carries the token cookie with it and lets the service turn away a request
that came from somewhere else. It binds `127.0.0.1` by default for the same
reason: the proxy is meant to be the only thing that reaches it. The token is
generated on first run into `--token-file`, and loading `/state?k=<token>` once
sets the cookie the pages use from then on.

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
