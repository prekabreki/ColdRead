# ColdRead — Next Steps Plan

Two epics, derived from a grilling session on how ColdRead is *actually* used
(not the full roadmap). This plan supersedes the roadmap's ordering for the
maintainer's own workflow.

## Context: who this is for

ColdRead's owner is the primary user. Real usage profile:

- **Solo performer voicing *all* characters** for **two YouTube channels**
  (narrated multi-character stories + documentary/mixed-archive pieces).
  Speaker color-coding is **load-bearing** — it's the voice-switch cue, not decoration.
- **Reads off an iPad in the booth.** Today: PDF exported → OneDrive → paged
  through in a PDF app.
- **ColdRead is already the daily driver.** Content is destined for YouTube, so
  there is **no privacy/NDA constraint** on hosting the formatted output.

This reframes the roadmap. The features that match this workflow are a small
subset; the rest are explicitly deferred (see "Out of scope").

## The two epics

### Epic 1 — iPad Read-View (the #1 pain: getting a good view onto the iPad)

A new render target alongside the PDF: a **self-contained HTML scroll-view**,
built for reading aloud off a tablet.

**Reading experience**
- Dark background, large type, generous leading.
- **Speaker color-coding preserved** from `assign_colors` (the voice-switch cue).
- Fixed **read-line at eye height**; text scrolls up to meet it.
- Active breath-group highlighted; the rest dimmed for a reading anchor.

**Control (manual — no auto-scroll fighting the performer's pace)**
- Touch-drag and tap zones.
- **Bluetooth foot-pedal / keyboard** support (pedals present as keyboards, so
  arrow/space/page keys drive scroll). Space/arrows = scroll, speed nudge.

**Delivery (two paths, same artifact)**
1. **File export** — a single self-contained `.html` (all CSS/JS inline, fonts
   embedded or system). Sync via OneDrive/AirDrop exactly like the PDF today;
   opens fullscreen in Safari; **works fully offline** once downloaded.
2. **Publish to URL** — push the finished view to a hosted URL for tap-to-open
   portability. **The pipeline stays local** — only the finished, already-public
   read-view is hosted; raw scripts never leave the machine. (Host TBD in
   implementation: Vercel, a static bucket, or GitHub Pages. Keep the publish
   step decoupled from the renderer.)

**Rough runtime** shown once, up front (word-count × per-channel WPM, with
number/acronym expansion). **No live HUD, no per-section timing** — the owner
explicitly does not need those.

**Architecture / integration points**
- New module `vo_format/read_view.py`: `render_read_view(blocks, path, toggles) -> str`.
  Mirrors `pdf_writer.generate_pdf` — consumes the same `list[FormattedBlock]`.
- `BlockType → CSS class` map, analogous to `pdf_writer.py:429` (`_STYLE_FOR`).
  Reuse `block.color`, `block.indent_level`, `block.bold/italic`.
- CLI: add an output-format flag near `cli.py:59-63`; dispatch near `cli.py:579`
  (where `generate_pdf` is called). Default stays PDF; `--html` (or `--format`)
  selects the read-view.
- GUI: add an export path/button near `gui.py:1852-1902`; extend
  `OUTPUT_FILETYPES` (`gui.py:64`). Respect intro/outro via the existing
  `_wrap_with_intro_outro` (`gui.py:538`) — read-view is post-formatter, same
  as the PDF.
- Breath-group segmentation comes from Epic 2. In the read-view there is **no
  page width to balance against**, so it breaks **purely** at breath boundaries
  (unlike the PDF, which caps to width).

**Acceptance (Epic 1)**
- `coldread <script> --html` produces one self-contained `.html` that opens
  offline in a browser, dark, large-type, speaker-colored.
- Manual scroll via touch + keyboard/pedal; fixed read-line; active breath-group
  highlighted.
- GUI can export the read-view and (separately) publish it to a URL.
- Runs against all five sample archetypes without error; verified on iPad Safari.

### Epic 2 — Meaning-first breath breaker (the #2 pain: breaks land mid-phrase)

Rework `vo_format/cold_read.py` from **width-first** to **meaning-first**.

**The current bug (by construction)**
- `wrap_cold_read` decides how many lines fit the page width, aims for balanced
  line lengths, *then* hunts for a break near that position.
- When no clause boundary sits near the width target, it falls back to an
  **arbitrary space** (`cold_read.py:201` and the `_find_best_break` fallback at
  `:132-135`). Nothing prevents splitting a proper noun or breaking after an
  article/determiner. That is the "mid-phrase / bad spot" problem.

**The fix**
- **Linguistic boundaries drive breaking**, width becomes a *fallback cap*:
  sentence (`.!?`) → clause (`;` `:` `—`) → comma → before-conjunction (the
  existing conjunction list at `:44-47` is a good start).
- **Protection rules** (grounded in Netflix/BBC subtitle line-breaking):
  - **Never break inside a proper noun** (don't split between two capitalized
    words).
  - **Never break after an article/determiner** (`a`, `an`, `the`, `my`, `your`,
    `this`, `these`, etc.) or between an article and its noun.
  - Prefer breaking *before* conjunctions/prepositions, not after.
- **Read-view mode:** width unconstrained → pure breath-group breaking (short
  lines allowed and welcome).
- **PDF mode:** keep width caps, but only as a fallback when a phrase genuinely
  exceeds the line — linguistic boundaries win first.
- **No new heavy NLP dependency** (no spaCy — it would bloat the PyInstaller
  bundle). Determiner/conjunction word-lists + capitalization checks are enough.

**Acceptance (Epic 2)**
- Test corpus of known-hard lines (proper nouns, article+noun, long clauses)
  never breaks mid-name or after an article.
- Existing `tests/test_formatter.py` still passes; add breaker unit tests.
- Improves the **current PDF** output (ship-able independently of Epic 1) and
  feeds the read-view's unconstrained breaking.

## Build sequencing

The owner chose to commit to **both epics**. Recommended internal order within
that commitment:

1. **Epic 2 first** — smaller, self-contained, improves the PDF used *today*,
   and is exactly what the read-view needs. Low risk, quick win.
2. **Epic 1** — the multi-day build, on top of the improved breaker. Renderer
   first (file export), publish-to-URL second (decoupled).

## Out of scope (deferred — real ideas, wrong fit for this workflow now)

> **⚠ Amended 2026-07-30 — two entries below were REVERSED and are now shipped.**
> Auto-scroll and local-server delivery both left this list and became the
> `coldread-readview` teleprompter, merged in `a9a54f0` and validated on the
> owner's iPad. Full reasoning: `docs/superpowers/specs/2026-07-30-pdf-teleprompter-design.md`.
> Anyone planning work from this document must read that spec first — the rest of
> this list still stands, but these two do not.

From the roadmap / competitive brief, explicitly **not** building now:

- Take-log, pickup marking, **stable line-IDs**, JSON sidecar (no need to mark
  pickups on the iPad → the whole line-ID plumbing is unnecessary here).
- **Persistent pronunciation lexicon** (owner did not rank re-fixing names as a
  top pain).
- **Per-section / live timing HUD** (a rough total is plenty).
- ~~**Auto-scroll / voice-tracking** (fights variable-pace character
  performance).~~ **REVERSED 2026-07-30 — auto-scroll SHIPPED.** The objection
  was right about *uncontrolled* auto-scroll. Touch-to-freeze, drag-to-reposition
  and release-to-resume answer it directly: the performer holds the pace, the page
  merely defaults to moving. Voice-tracking remains out of scope.
- **Fountain / FDX / RTF / SRT import** (not the owner's input formats).
- **Document furniture** — page numbers, widow/orphan, MORE/CONT'D (irrelevant
  when scrolling, not paging).
- **Round-trip verification** (`--verify`) — nice trust feature, not a felt pain.

Also reversed, from Epic 1's own scope note rather than this list:

- ~~Local live-server delivery ("live later" — a possible future add-on).~~
  **REVERSED 2026-07-30 — the server is the DELIVERY PATH, not an add-on.** The
  epic assumed a file synced through OneDrive would open on the iPad. It does not:
  iOS previews a local `.html` rather than running it, so the artifact is
  undeliverable without HTTP. An always-on Pi on the LAN also removes the
  rig-must-be-awake objection entirely.

These stay in `docs/ColdRead-roadmap.md` for if the audience ever widens beyond
the maintainer.

## What actually shipped (2026-07-30)

`coldread-readview <pdf>` renders a finished ColdRead PDF into one self-contained
auto-scrolling HTML page. It reads the PDF's embedded text layer, so speaker
colours, bold/italic, the size hierarchy and the cold-read breath-group line
breaks all carry over exactly — and pages stop existing, which is what removes the
paging friction Epic 1 was written to solve.

Notably this **did not need Epic 2 (the meaning-first breath breaker, #3) or the
render-op layer (#31)**, both of which #4 was blocked on: the breath groups are
already baked into the PDFs by `--cold-read-breaks`, and reading a finished PDF is
a different input from the `FormattedBlock` stream those issues operate on. Epic 1
(#4), which renders from source, remains open and still depends on both.

Confirmed working on device: library index, tap-through, auto-scroll, hold-to-
freeze/drag/release, font resize, light/dark toggle, wpm control with
hold-to-accelerate. **Not** working: keep-awake — `navigator.wakeLock` needs a
secure context, so it awaits HTTPS.
