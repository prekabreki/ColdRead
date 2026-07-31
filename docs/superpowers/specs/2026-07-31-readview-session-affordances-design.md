# Read-view session affordances: momentum, progress, resume mark, length

**Date:** 2026-07-31
**Status:** approved, implementing

Four adjustments from the first real sessions on the iPad. Three are in the
read-view, one is on the library index. They share a theme: a script is read
across more than one sitting, and nothing in the page acknowledges that.

## 1. Momentum scroll

`touchmove` already seeks 1:1 and `touchend` stops dead. Dragging to reposition
therefore feels like scrubbing a filmstrip rather than scrolling a page, and
covering distance takes repeated drags.

`touchmove` keeps its 1:1 seek and additionally records a velocity sample
(position, timestamp). Only samples from the **last 80 ms** count, so a drag that
decelerates to a stop before the finger lifts flings nothing — which is how a
deliberate "put it exactly here" gesture stays exact.

On `touchend`, if `|v| > 40` px/s a `requestAnimationFrame` glide runs
`pos += v * dt` with exponential decay `v *= pow(0.94, dt / 16)`, ending below
20 px/s or at either end of the scroll range.

A new `touchstart` cancels the glide immediately. A finger down always means
"stop here" — that is the existing freeze contract, and it is the one gesture
that must never feel laggy.

**Interaction with autoscroll:** the glide owns the scroll while it runs, and
`held` stays true until it finishes, so a playing script does not fight the
fling. When the glide ends, `lastFrame` is reset and autoscroll resumes from
wherever the glide landed. Flinging during playback reads as "throw ahead,
resume from there".

CSS needs no change: `touch-action: none` already means the page owns scrolling,
so there is no native momentum to conflict with.

## 2. Completeness percentage in the HUD

`percent()` is `round(pos / maxScroll() * 100)`, clamped to 0–100, painted from
the existing `seek()` path. Two consumers:

- `#status` becomes `"42% · 150 wpm"`, plus `" ▌▌"` when paused. Its
  `min-width` goes 11ch → 15ch.
- `#hud::after`, 3 px on the HUD's top edge, `width: var(--progress, 0%)`. The
  HUD's existing `border-top` reads as the unfilled track. **No CSS
  transition** — it repaints every frame, and a transition would drag the fill
  behind the number beside it.

`--progress` is set on `#hud`, **not** on `documentElement`. It is written every
frame of a scroll, and a custom property on the root invalidates style for
everything that might inherit it — which is the whole script.

A pseudo-element rather than a real `<div id="bar">`: nothing extra in the DOM to
be missing, nothing for `reader.js` to have to find before it can paint, and
nothing inside `#hud` for the existing markup tests to trip over.

`maxScroll()` already accounts for the 50vh lead-in and run-out, so 100%
coincides with the last line reaching its resting place. When the script is
shorter than the viewport `maxScroll()` is 0; that reports 100%, which is
correct rather than a divide-by-zero.

## 3. Hold a word to mark the resume point

**No DOM change to the script body** — no per-word spans. A 3000-word script
would mean 3000 elements to serve, restyle on every `A+`, and keep offsets
into.

`touchstart` on the script starts a 500 ms timer. Movement past 8 px, or a lift,
cancels it, so it never competes with drag or fling. `mousedown`/`mouseup` feed
the same timer, which is what makes the feature checkable without a touchscreen.

On fire:

1. `document.caretRangeFromPoint(x, y)` — WebKit and Blink — with a
   `document.caretPositionFromPoint` fallback, locates the text node and offset.
2. `lineOffset()` converts that to an offset into the **line's whole text**, by
   summing the preceding text nodes. This matters because a line that currently
   holds the `<mark>` has three text nodes, so a caret offset into one of them is
   not a line offset. Measuring line-relative means a press resolves identically
   whether or not the mark is already in that line — no tearing the highlight
   down first in order to measure.
3. The offset expands to word boundaries by scanning with `/\s/`.
4. If the resolved span equals the current mark's span it is cleared; otherwise
   the mark moves there. **One marker**, so "where do I resume" has exactly one
   answer. Comparing spans rather than what the caret landed *inside* means
   pressing any part of the marked word clears it, including its first character.

**Every failure path leaves the mark alone.** A press that lands in the gutter
beside the text, or between lines, must not destroy the resume point it was
aiming for.

`commitMark()` is the only writer: it clears, repaints, and persists in one step,
so the DOM and storage cannot disagree. An earlier shape cleared the highlight
and *then* returned early on some paths, which left storage holding a mark that
was no longer on the page — it reappeared on the next load.

The highlight is a `<mark>` created with `Range.surroundContents`, amber
(`rgba(255, 184, 77, …)` background, inherited text colour) so it survives both
themes and does not cost a coloured character line its voice-switch cue.
Clearing calls `normalize()` on the paragraph.

**Storage:** `coldread:<title>:mark` = `{line, start, end, text}`. `line` indexes
`document.querySelectorAll(".l")`, which includes the two header paragraphs —
deliberately, since the index only has to be self-consistent. `text` is a guard:
on load the mark restores only if the characters at `[start, end)` still match,
and is dropped silently otherwise.

The versioned store key (`data-title` is the PDF's `path.stem`, which carries the
draft version) already means a new draft cannot inherit an old mark. The `text`
guard is belt-and-braces, justified because the failure it prevents — a
highlight sitting on the wrong word — is silent and actively misleading.

**On load the mark beats the saved position.** If a valid mark restores, the page
scrolls to put it ~40% down the viewport instead of restoring `pos`. The mark is
a deliberate declaration; `pos` is incidental. The cost is that reading past the
mark and returning rewinds to it.

`-webkit-touch-callout: none` joins the existing `user-select: none` on `body`,
or iOS raises a share sheet on the 500 ms press.

## 4. Word count on the library page

`render.py` stamps `data-words="{script.word_count}"` on `<body>`.
`library.py`'s `entry_for` reads a bounded prefix (`_PREFIX_BYTES = 16384`) and
pulls the number with `data-words="(\d+)"`; missing or unparseable yields `None`.
Decoding replaces errors rather than raising, since a prefix read can slice a
multi-byte character in half.

A bounded read rather than whole files keeps the index pass off every script's
body on a memory-tight Pi, and means a truncated or half-written read-view cannot
raise mid-index.

The bound needs real headroom and a test pins it. The attribute currently lands
at byte ~4337, behind the inlined CSS — **which grows ahead of it**. The first
draft of this used 4096 and would have shipped a feature that was completely
dead: every library row renders perfectly, just with no length on it.

`IndexEntry` gains `words: int | None`. `_row` renders
`1,340 words · 2026-07-31`, degrading to date-only when `None`.

Rejected: encoding length in the filename (the publisher and the far end already
agree on filenames; adding a field invites drift) and a sidecar JSON (a second
file to keep in sync with the first).

## Testing

`tests/test_readview_library.py` gains:

- word count rendered with thousands separators
- `None` degrading to date-only
- `entry_for` extracting `data-words` from real temp files: present, absent,
  malformed
- `render()` emitting `data-words` — this is the contract between the two
  modules, and the one thing that can silently break the feature

The three read-view behaviours get **no automated test**, matching the precedent
the library index spec set for its touch handler. They were instead driven in
headless Chromium on the rig (playwright-core + the cached chromium), which
turned out to reach further than expected — CDP `Input.dispatchTouchEvent`
produces genuine touch events, so momentum was verifiable here after all.

Verified 2026-07-31, no JS errors in any run:

| Behaviour | Result |
|---|---|
| percentage + bar via `PageDown`/`End`/`Home` | 0% → 100% → 0%, bar width tracks |
| fast flick | glides 389 px past the lift, decaying |
| flick then hold still 250 ms | 0 px drift — the stale-sample guard works |
| slow positioning drag (300 px / 1.4 s) | lands exactly, 0 px drift |
| finger down mid-glide | stops dead, 0 px |
| fling while playing | glides, then autoscroll resumes at 150 wpm from the landing point |
| long-press a word | marks it; storage matches |
| drag past `HOLD_SLOP` | no mark, as intended |
| press the gutter beside the text | existing mark survives |
| press the marked word's first character | clears it, both DOM and storage |
| reload with a mark | restores and lands the word at 0.40 of the viewport |
| reload with a *stale* mark | dropped from DOM and storage |

Still iPad-only, and stated rather than implied: whether the glide *feels* right
under a real thumb, and whether the wider `#status` fits the HUD in portrait.

## Out of scope

Marking a script read in the library when the read-view reaches 100%. The
percentage makes it tempting, but inferring completion from scroll position has
its own failure modes and the library spec already parked it.
