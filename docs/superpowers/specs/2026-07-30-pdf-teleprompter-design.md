# PDF Teleprompter — design

**Date:** 2026-07-30
**Status:** approved design, not yet implemented
**Supersedes:** two decisions recorded in `docs/PLAN.md` (see [Reversals](#reversals))

## Problem

The maintainer records voice-over off an iPad in a booth. Today's flow is
ColdRead PDF → OneDrive → page through a PDF app, and paging is the primary
friction: it interrupts delivery, and a paginated document forces a page turn at
whatever point the typesetter happened to land on.

Wanted instead: the script pans down slowly on its own, and can be grabbed and
stopped, or sped up and slowed down, from the device.

## Approach

Read the **already-derived PDFs** rather than re-rendering from source scripts.

This is the decisive choice, and it is not a compromise. The PDFs are
born-digital — ReportLab embeds a real text layer, so extraction is exact
rather than approximate — and everything ColdRead puts in is recoverable.
Measured on 2026-07-30 across all three shapes of output in the two ready
folders:

| Recovered | Evidence in the PDFs |
| --- | --- |
| Speaker colors | `#2563eb` blue speaker cue, `#dc2626` red, `#6b7280` gray parentheticals |
| Bold / italic | encoded in the font name: `CourierNewPS-BoldMT`, `-ItalicMT` |
| Size hierarchy | 18/14 headings, 12 body, 10 pronunciation hints |
| Indent level | line `x0`: 64 = speaker label, 100 = body, 138+ = deeper |
| Cold-read line breaks | 1030 / 768 / 1062 lines, each already one breath group |

Consequences that make this the right base:

- **The breath-group line breaking is already done.** `--cold-read-breaks`
  baked it into the PDF. No dependency on #3.
- **It works on all ten existing scripts immediately**, with no re-derivation.
- **It does not depend on the render-op layer (#31)**, which operates on
  `FormattedBlock`s from the formatter — a different input entirely.
- **Pages stop existing**, so the page-gap problem is not fixed, it is gone.

The work splits across two machines along a natural seam:

```
                    RIG (LinuxHeima)                     │      PI (raspberrypi.local)
                                                         │
 ~/OneDrive/CL/ready/*.pdf  ──┐                          │
 ~/OneDrive/BoP/ready/*.pdf ──┴─► extract ──► render ──► │  serve ──► iPad / Pi screen
                                 (PyMuPDF)   (HTML str)  │  (stdlib only)
                                     │           │       │      ▲
                                list[ReadLine]  one .html│      │
                                                per script──rsync┘
                             `coldread-readview`         │  `coldread-serve`
```

Extraction is heavy and stays on the machine that already has PyMuPDF. Serving
is trivial and lives on the always-on box. The Pi Zero 2 W has **416MB RAM
total, ~231MB available**, and runs Pi-hole for the whole house's DNS —
`unattended-upgrade --dry-run` alone nearly OOMs it — so nothing beyond the
stdlib may be imported there.

### Why the Pi hosts it

- **Always available.** Rig asleep, off, or rebooting is irrelevant. The
  offline-fallback problem does not get a fallback; it stops existing.
- **Smallest reviewable attack surface.** The Pi-side server does exactly one
  thing: send bytes for files in a list it built itself. No PDF parsing, no
  rendering, no pipeline, nothing that touches `~/OneDrive`.
- **No OOM risk** next to the house's DNS.

The rendered file is the transport, not a second delivery path — there is one
renderer and one artifact. That it also syncs to the iPad through OneDrive and
opens with no server at all is a side effect, and it is what makes Phase 1
below possible.

## Components

**Purely additive in Python.** No existing module changes: `cli.py`,
`formatter.py`, `pdf_writer.py`, `models.py`, `colors.py`, `cold_read.py` are all
untouched. In a public repo that is the point — the diff cannot regress the tool
in daily use. Three non-Python files do change: `pyproject.toml` gains two entry
points, `README.md` gains a security-posture section, and `docs/PLAN.md` records
the reversals below.

New package `vo_format/readview/`:

| Unit | Responsibility | Depends on |
| --- | --- | --- |
| `extract.py` | `extract_lines(pdf_path) -> ReadScript` — pure; one file in, dataclasses out | PyMuPDF only |
| `render.py` | `render(ReadScript) -> str` — one self-contained HTML string | nothing |
| `cli.py` | `coldread-readview <pdf>…` — glue; writes files, reports counts | the two above |
| `serve.py` | `coldread-serve` — index, token, bytes | **stdlib only** |

Two new entry points in `[project.scripts]`: `coldread-readview` and
`coldread-serve`. Not argparse subcommands — `vo_format/cli.py` is a single flat
parser with a positional `script` argument, and adding subparsers would break
the existing `coldread <script>` invocation.

**`serve.py` imports nothing from `vo_format` and nothing from PyPI.** It is a
leaf module, copied to the Pi with `scp`, never `pip install coldread` — that
would drag reportlab, anthropic, and customtkinter onto a 416MB headless box.
This constraint is enforced by a test that AST-walks its imports against a
stdlib allowlist, so it cannot rot silently.

## Data model

```python
@dataclass(frozen=True)
class ReadLine:
    text: str
    color: str          # "#2563eb", as found in the PDF
    bold: bool          # from font name: "-BoldMT"
    italic: bool        # from font name: "-ItalicMT"
    size_ratio: float   # this line's pt size ÷ the document's modal size
    indent: int         # in character widths, relative to the doc's leftmost line
    gap_before: bool    # a paragraph break preceded this line

@dataclass(frozen=True)
class ReadScript:
    title: str          # PDF filename stem
    lines: list[ReadLine]
    word_count: int     # drives the wpm scroll rate
    page_count: int     # source pages, for the extraction canary
    derived: str        # source PDF mtime, ISO 8601 — the staleness signal
```

Three derivations are where fidelity is won or lost:

- **`indent` is relative, not absolute.** The leftmost line sits at x0 = 79.2 or
  63.6 depending on the margin preset in use, so absolute points misread
  indentation across scripts. Measure against the document's own minimum x0 and
  bucket by Courier's advance width (0.6 em).
- **`size_ratio` is relative to the modal size, not hard-coded.** The observed
  18/14/12/10 hierarchy shifts with `--font-size`. A ratio lets the reader pick
  a base size and scales the whole hierarchy with it.
- **`gap_before` reconstructs paragraph structure** from vertical spacing: a gap
  materially larger than normal leading was a blank line. Without it, 1030
  breath groups collapse into an undifferentiated wall.

## Rendering

One HTML file per script. All CSS and JS inline. Zero network requests after
load.

### Color: the palette is built for white paper

`colors.py` says so explicitly — *"optimized for print grayscale"* — and the
narrator is `#000000`. On a dark booth background the body text is invisible and
`#2563EB` has poor contrast. But those colors are the **voice-switch cue** for a
performer voicing every character, so they cannot be discarded.

Resolution: an explicit dark-mode twin table covering **all eleven colors
`colors.py` can emit** — the eight `PALETTE` hexes plus `NARRATOR_COLOR`
(`#000000`), `STAGE_DIRECTION_COLOR` (`#6B7280`), and `SOUND_CUE_COLOR`
(`#9CA3AF`) — mapping each to a dark-legible counterpart **at the same hue**, so
a given character stays the same color, just a readable version of it. Any color
absent from the table falls through to an algorithmic lightness lift, so an
unknown color degrades gracefully instead of vanishing.

Two constraints on the dark theme, both checkable:

- **The background is near-black, not pure black** (around `#121212`). Pure black
  behind bright monospace produces halation that blurs glyph edges at booth
  distance. Body text is correspondingly a soft off-white, not `#FFFFFF`.
- **Every mapped color clears WCAG AA (4.5:1) against that background**, and the
  eight character colors stay mutually distinguishable **in grayscale** — the
  same property `colors.py` protects for print, preserved for the screen. A
  test asserts both, so a future palette edit cannot quietly break the
  voice-switch cue.

A light-mode toggle reproduces the PDF's exact colors on white, as an escape
hatch if dark mode ever misleads about who is speaking.

### Reading behavior

Even brightness throughout. **No read-line marker, no active-line highlight, no
dimming** — deliberately chosen against `docs/PLAN.md`, which specified
highlight-and-dim for a *manual*-scroll design. Under auto-scroll there is
nothing to lose your place by, and dimming costs the lookahead that phrasing and
breath planning come from.

**Scroll** is a `requestAnimationFrame` loop advancing sub-pixel offsets.
`setInterval` + `scrollBy` produces visible per-tick stutter at slow rates.

**Speed is words per minute**, not an abstract multiplier. The extractor knows
the word count, so "155 wpm" is a real number that can be matched to actual
delivery and means the same thing across a 1030-line script and a 768-line one.
`−`/`+` step by 5 wpm, default 150.

The conversion is explicit, because "wpm" alone does not determine a pixel rate:

```
words_per_line = word_count / len(lines)          # measured per script, ~7 on these PDFs
lines_per_sec  = (wpm / 60) / words_per_line
px_per_sec     = lines_per_sec * rendered_line_height_px
```

`rendered_line_height_px` is read from the DOM after layout rather than assumed,
so the rate stays honest across font-size and orientation changes.

**Three control paths:**

| Input | Behavior |
| --- | --- |
| Touch | finger down freezes · drag repositions · lift resumes at the same wpm |
| `−`/`+` edge zones | persistent, fixed position, hittable without looking |
| Keyboard | space = pause/resume · ↑↓ = wpm · PgUp/PgDn = reposition |

Keyboard support is load-bearing, not a nicety: a **foot pedal presents as a
keyboard**, and the eventual **Pi with a screen** may have no touch input. It
gets tested.

### Keeping the screen awake — and why the obvious API is not enough

Without intervention the iPad dims and sleeps mid-read. That is the difference
between a teleprompter and a demo, so it needs to actually work.

**The Screen Wake Lock API requires a secure context.** Neither delivery path
here provides one: `file://` is an opaque origin, and `http://192.168.50.181:8765`
is plain HTTP on a LAN address. So `navigator.wakeLock` will be **undefined in
both Phase 1 and Phase 2**, and a design that relied on it would fail in the
booth, not in testing.

Three layers, in order:

1. **A muted, looping, 1-frame inline `<video>`** playing while scrolling. This
   is the long-standing iOS keep-awake mechanism and it needs no secure context.
   The video is a data URI, so self-containment holds.
2. **`navigator.wakeLock` when it exists**, used in preference to the video.
   It becomes available if the page is ever served over real HTTPS — notably
   `tailscale serve`, which issues a genuine certificate for the tailnet
   hostname. Worth knowing as a future upgrade, not a Phase 2 requirement.
3. **Documented fallback:** iPad Settings → Display & Brightness → Auto-Lock →
   Never. Zero code, and the thing to reach for if layers 1 and 2 both
   disappoint on a given iOS version.

**Client-side state.** Scroll position, wpm, font size, and light/dark persist
to `localStorage`, per script. This is also a security property: **the server
needs no write endpoints at all**, so there is nothing on it to POST to.

`localStorage` access is wrapped in a try/except that degrades to in-memory
state, because **Safari blocks storage for `file://` origins**. In Phase 1 that
means preferences reset on reload; in Phase 2, served over HTTP with a real
origin, persistence works. The reader must not throw on the file path — a
teleprompter that refuses to render because it cannot save a font size is worse
than one that forgets it.

**Font size** on `A−`/`A+`, default 20px. Measured fit on an 11" iPad portrait
(834 CSS px, 32px side padding):

| Font size | Chars that fit |
| --- | --- |
| 16px | 80 |
| 20px | 64 |
| 24px | 53 |
| 28px | 45 |

Against measured line lengths — median 42, p95 54–62, max 62/81/63 — **20px
portrait holds essentially every line unwrapped**. Larger type means rotating to
landscape (78 chars at 24px) or accepting occasional wraps. When a line does
exceed the width, its continuation gets a hanging indent so a wrapped breath
group still reads as one unit rather than masquerading as two.

## Server

Stdlib `ThreadingHTTPServer`, roughly 120 lines. **GET and HEAD only**; every
other method is a flat 405.

**Routing is by allowlist, never by path arithmetic.** At startup the server
enumerates its library directory once into `{slug: absolute_path}` and
thereafter routes only by dictionary lookup. Client input is never joined to a
path, never normalized, never resolved — it is a key or it is a 404. Symlinks
are not followed. There is no directory-listing handler; `/` renders the index
the server built itself.

The index groups scripts by the **top-level subdirectory name** under the library
root — `coldread-library/CL/` and `coldread-library/BoP/` become the two sections,
via a display-name map so they read as "CassetteLore" and "Birds of Play". The
scan is one level deep only; a new channel is a new subdirectory and needs no
code change.

**Auth.** A 256-bit urlsafe token generated on first run, saved to the Pi's
config. The bookmark carries `?k=…`; the first request sets an `HttpOnly`,
`SameSite=Strict` cookie so later requests authenticate without the token
riding in every URL. Compared with `hmac.compare_digest` — a timing oracle on a
token is real even on a LAN.

Three subtleties, on the record so they are not rediscovered:

- **The token does real work.** The Pi is a tailnet node
  (`100.117.152.52`), so this is reachable from anywhere, not only the home
  wifi. "It's only on my LAN" stopped being a defense the moment the Pi became
  the host.
- **Strict `Host` equality does not fit.** The usual loopback recipe pins `Host`
  to one exact value, but this server has three legitimate names:
  `raspberrypi.local`, `192.168.50.181`, and the tailnet address. `Host` is
  therefore checked against a configured allowlist seeded from the addresses
  detected at startup, and `Origin` / `Sec-Fetch-Site` are rejected when present
  and not same-origin. Absence is allowed — a bookmark tap sends neither.
- **`http.server` logs the full request line by default**, which would write the
  token to the journal on every request. `log_message` is overridden to strip
  the query string.

## Publish and staleness

`coldread-readview 'CL/ready/*.pdf' 'BoP/ready/*.pdf'` writes
`<title> - readview.html` beside each PDF, mirroring the existing
`- formatted.pdf` convention. **Idempotent**: skips any HTML newer than its
source PDF unless `--force` is passed, so re-running across both folders does
only the work that is needed.

Publishing is a documented rsync pair, deliberately **not code** — one
invocation per channel, each with its own destination:

```bash
for ch in CL BoP; do
  rsync -av --delete --include='*.html' --exclude='*' \
    "$HOME/OneDrive/$ch/ready/" "raspberrypi.local:~/coldread-library/$ch/"
done
```

One `rsync` with two sources ending in `/` would merge both channels' contents
into a single flat destination and lose the CL/BoP distinction entirely — and
`--delete` with multiple sources is not well defined. Hence the loop.

A `--publish` flag would put subprocess-invoking-ssh into a public package to
save one command that lives in shell history anyway. Adding it later is trivial;
removing it would not be.

**Staleness is visible, not prevented.** The Pi cannot see the rig's PDFs, so it
cannot know it is behind. Each index row shows its derivation date instead. This
is an honest limitation rather than a solved problem: better to read "derived 3
days ago" and think about it than to trust a green checkmark the Pi has no way
to earn.

## Failure behavior

This repo has been bitten twice by the same failure shape — **#138 and #139 are
both silent content loss at exit 0**. The anti-silence rule is therefore
explicit:

- Every file prints its canary: `extracted 1030 lines from 34 pages`. Line count
  tracks the script, exactly as `Formatting... done (N blocks)` does today; a
  three-digit drop is visible.
- **Zero extracted lines is a hard failure** — named, non-zero exit, no HTML
  written. A PDF that is not a ColdRead PDF, is encrypted, or is genuinely
  image-only must fail loudly rather than produce an empty read-view.
- The server refuses to start on a missing library directory, an unreadable
  token file, or a busy port, each with a specific message. It never serves an
  empty index: an empty index is indistinguishable from "everything is fine and
  you have no scripts."

**Known-bad output is reproduced, not laundered.** #139 means the existing PDFs
already contain leaked literal asterisks, and the extractor will faithfully
carry them through. Stripping stray `*` in the reader would hide an open bug.
Fix #139, re-derive, and read-views come out clean; until then the asterisks
stay visible on purpose. Likewise #138: a PDF that lost its outro yields a
read-view that lost its outro. The reader cannot recover what is not there.

## Testing

Reading PDFs makes a real round-trip test available:

- **`extract.py`** — a fixture generates a PDF from each bundled sample through
  the existing pipeline, then extracts it and asserts every source line
  survives, colors map, indents map, and `gap_before` reconstructs the paragraph
  breaks. Generated in-test, never committed as a binary fixture: a committed
  fixture is how a `.gitignore` rule silently excludes it while local runs stay
  green forever.
- **`render.py`** — self-containment assertions: no `<script src`, no `@import`,
  no `url(` leaving the document, no `http`/`https` anywhere in the output. The
  token never appears in rendered HTML.
- **`serve.py`** — the stdlib-only AST import check; traversal attempts (`../`,
  `%2e%2e`, absolute paths, unknown slugs) all 404; token absent / wrong /
  correct; `POST` → 405; and a log-scrubbing test asserting the token never
  reaches a log line.
- Tests are **mutation-checked**, not merely read. Assertions in this repo have
  passed for the wrong reason before.
- On-device verification is part of done: **iPad Safari and Chromium on the Pi**,
  since the Pi is the one that may have no touch.

## Build order

Phased so the first phase is usable in a booth on its own, with no server, no
token, and no network surface at all.

**Phase 1 — read it tonight.** `extract.py`, `render.py`,
`coldread-readview`, and their tests. The HTML lands in the OneDrive `ready/`
folders, syncs to the iPad on its own, and opens from Files. Full teleprompter:
auto-scroll, touch and keyboard control, keep-awake via the video layer, font
size, dark mode. No server exists yet, so none of the security surface does
either. Known Phase 1 limitation: preferences do not persist across reloads,
because Safari blocks `localStorage` on `file://`.

**Phase 2 — the Pi.** `serve.py`, the index, token auth, the security checks,
and the rsync line. Turns "find the file in OneDrive" into "tap a bookmark".

**Phase 3 — polish, driven by actual booth use.** Deliberately unspecified;
the first two recording sessions decide what belongs here.

## Reversals

Two decisions in `docs/PLAN.md` are reversed. Both must be amended there, with
reasons, or a future session — or a dispatched executor reading the doc — will
rebuild the superseded design.

| Recorded decision | Now | Why |
| --- | --- | --- |
| "Auto-scroll / voice-tracking" listed **out of scope**, reason: *"fights variable-pace character performance"* | **In scope, and the core of the feature** | The objection was correct about uncontrolled auto-scroll. Touch-to-freeze, drag-to-reposition, release-to-resume answers it directly: the performer holds the pace, the page merely defaults to moving. |
| "Local live-server delivery ('live later')" listed **out of scope** in issue #4 | **In scope**, hosted on the Pi | An always-on LAN box changes the cost. The rig no longer has to be awake. |

## Relationship to open issues

| Issue | Relationship |
| --- | --- |
| **#4** Epic: iPad HTML scroll read-view | **Sibling, not parent.** #4 renders from `FormattedBlock`s and is blocked by #3; this reads finished PDFs and is blocked by nothing. Folding this into #4 would make #4 unbuildable as written. #4's "out of scope: local live-server delivery" line is now false and must be removed. |
| **#31** RENDER-OP | Claims to unblock "the HTML read-view #4" — true for #4, **false for this**. Needs a comment so nobody sequences this work behind it. |
| **#3** meaning-first breath breaker | Does not block this; breath groups are already in the PDFs. When #3 lands, re-deriving upgrades every read-view for free. |
| **#139** asterisk leak | Visible in read-views until fixed. Deliberately not masked. |
| **#138** metadata strip deletes content | A truncated PDF yields a truncated read-view. |
| **#68** PyMuPDF AGPL vs MIT-labeled bundles | Unaffected and still open. Worth noting that `serve.py` is stdlib-only, so the Pi-side artifact carries no AGPL code. |
| **#137**, **#37–#44** GUI issues | **The GUI is deliberately excluded from this work.** Zero test coverage and eight open bugs; adding surface to it now invites trouble. CLI only. |

## Out of scope

- **The GUI.** No button, no export path, no preview integration.
- **Rendering read-views from source scripts.** That is #4, and it needs #3.
- **A `--publish` flag.** One rsync line, documented.
- **Take logs, line IDs, pickup marking, JSON sidecar** — unchanged from
  `docs/PLAN.md`; still not needed.
- **Live timing HUD or per-section timing.** The wpm control is the only
  timing-adjacent surface.
- **Mirror mode** for teleprompter glass. Reading off a tablet, not glass.
- **Service-worker offline caching.** The synced HTML file already covers the
  realistic failure.
