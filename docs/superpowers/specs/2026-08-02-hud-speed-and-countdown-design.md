# HUD pass: grouped speed control and a time-remaining countdown — design

Two changes to the read-view HUD, from using it in a booth. They are grouped
because they touch the same three files and because they pay for each other: the
speed regroup frees the horizontal room the countdown needs.

Supersedes the first acceptance criterion of #141 (the edge strips) and leaves its
second (the doubled title) alone.

## Problem

**The speed control is split and invisible.** Words-per-minute can be changed from
four places: two unlabelled full-height strips at the screen edges (`.zone`, at
`opacity: 0.22`) and two HUD buttons sitting on either side of the status readout.
The owner could not find the strips at all — the wpm number lives in the HUD, so
that is where the hand goes — and the two HUD buttons read as unrelated because
the status text separates them. Four controls, one function, no grouping.

**There is no answer to "will this fit the time I have".** The HUD reports
progress as a percentage and as a fill along its top edge. Neither tells you how
long is left, which is the question actually being asked of the page before a
take.

## Approach

One grouped, visible speed control, and a countdown derived from the scroll engine
rather than from a second source of truth.

### The speed cluster

Delete both `.zone` strips and both existing HUD wpm buttons. Emit one cluster:

```html
<div id="speed">
  <button id="wpmdown" type="button" aria-label="Slower">&minus;</button>
  <button id="wpmup" type="button" aria-label="Faster">+</button>
</div>
```

`−` above `+`, stacked, at the bottom-right.

**The cluster is a child of `#hud`, positioned `absolute` against it:**

```css
#speed {
  position: absolute;
  right: 12px;
  bottom: calc(100% + 12px);
}
```

That single decision is doing three jobs, and each of them is a bug avoided
rather than a preference:

- `#hud` is already `position: fixed`, so it is the containing block. The cluster
  therefore tracks the HUD's *actual* top edge, including the
  `env(safe-area-inset-bottom)` inside its padding. A `--hud-height` variable
  would have to be kept in sync with that padding by hand, and would be wrong on
  any device with a different inset. (`#hud::after` already relies on this same
  property; the CSS says so.)
- The touch handler exempts `#hud` from the freeze-and-drag gesture
  (`reader.js:367`: `e.target.closest("#hud, .zone")`). A control placed anywhere
  else in the DOM would scrub the script under the finger on its way to being
  pressed — which is precisely why the existing HUD comment says the `← Library`
  button lives where it does. Being inside `#hud` inherits that exemption with no
  new selector, and the `.zone` half of it can then be dropped with the strips.
- `#hud` is `display: flex`; an absolutely positioned child is out of flow, so the
  HUD row keeps its current single-row layout and height.

Styling: the same `var(--chrome)` fill as the other HUD buttons, over a solid
`var(--bg)` so the cluster **occludes** the moving text cleanly instead of
blending into it. Both buttons keep the existing 44px minimum target. The
`z-index` comes from `#hud` (11), so the cluster sits above the script and needs
none of its own.

`holdRepeat` is already bound to `#wpmdown` / `#wpmup` and already calls
`e.preventDefault()` on `touchstart` to suppress the synthetic click. Keeping
those two ids means the JS change is deletions only: the `el.slower` / `el.faster`
lookups and their two `holdRepeat` calls.

**What is lost, on the record.** The strips were the only speed control reachable
without looking, anywhere along either screen edge; the cluster is a small target
that has to be aimed at. This is a deliberate trade, taken because an
undiscoverable control is worth less than an aimed one, and because bottom-right
is where the thumb already rests on a tablet held in one hand. If eyes-free
adjustment turns out to matter more than expected, the answer is a keyboard or
foot-pedal binding — which `reader.js` already has — not restoring a 0.22-opacity
strip.

### The countdown

```js
var secondsLeft = (maxScroll() - pos) / pxPerSecond();
```

Derived from the scroll engine, not from `data-words`. Three reasons, in order of
weight:

1. **It cannot disagree with the page.** This *is* the time until autoscroll
   reaches the end, by construction. A word-count estimate reduces to the same
   arithmetic — `pxPerSecond` is `(wpm / 60) / wordsPerLine * lineHeight`, so
   remaining-pixels over that is remaining-lines × words-per-line ÷ wpm — but it
   is a parallel calculation that can drift from the one driving the scroll.
2. **No new dependency.** `data-words` is present on `<body>` but an older
   read-view may not carry it; `library.py` already has a whole `words_in`
   fallback path for exactly that. The scroll route needs nothing that is not
   already load-bearing.
3. **It follows changes automatically.** If `pxPerSecond` is ever redefined, the
   countdown stays correct with no second edit.

`pxPerSecond()` already measures real line height via `getComputedStyle` on a
body-sized `.bl` line, with the wrap trap already handled. Reuse it as-is.

Rendered in the existing `paintHud()`, which means it repaints on every scroll
frame, every wpm nudge, and every `A±` — the last of these matters, because
changing type size changes `scrollHeight` and therefore the estimate.

```
42% · 150 wpm · 6:12
```

- Format `m:ss`, with an `h:mm:ss` form above an hour.
- Shown while paused as well as while playing. "Does this fit" is a question asked
  parked, and the estimate is a function of the wpm setting, not of whether the
  page is currently moving.
- `#status` widens from `15ch` to `22ch`. The HUD can afford it because two
  buttons just left the row.
- A script shorter than the viewport gives `maxScroll() <= 0` and reads `0:00`,
  consistent with `percent()`'s existing "entirely on screen means done" guard.
- No divide-by-zero is reachable: `WPM_MIN` is 40 and `lineHeightPx()` has a
  non-zero fallback, so `pxPerSecond()` is always positive.

## Components

| File | Change |
|---|---|
| `render.py` | drop the two `.zone` divs and the two in-row wpm buttons; emit `#speed` inside `#hud` |
| `reader.css` | delete the `.zone` rules; add `#speed`; widen `#status` |
| `reader.js` | delete `el.slower` / `el.faster` and their `holdRepeat` calls; add the countdown to `paintHud()`; drop `.zone` from the touch exemption selector |
| `README.md` | the `**Controls:**` paragraph — the edge-strip sentence becomes false |

`README.md` is shared with #144, which has been constrained not to touch that
paragraph. This change owns it.

## Error handling

There is no failure path to speak of — no I/O, no network, no storage. The two
degradations worth naming:

- A missing `.bl` line makes `lineHeightPx()` fall back to `size * 1.55`, so the
  countdown is approximate rather than absent. Correct: a slightly wrong estimate
  beats a blank readout.
- `maxScroll()` is recomputed per call rather than cached, so an orientation
  change needs no invalidation hook.

## Testing

- `render.py`: `#speed` is emitted **inside** `#hud` with both buttons; no
  `class="zone"` remains anywhere in the output. The nesting assertion is the one
  that matters — outside `#hud` the page still looks right and the drag bug is
  invisible to a DOM test.
- `reader.css`: `#status` min-width is at least `22ch`; no `.zone` selector
  survives.
- The self-containment tests already in `test_readview_render.py` cover the
  countdown implicitly (no new external reference), and must keep passing.
- Countdown arithmetic is JS and this repo has no JS test runner. The expression
  itself is two terms over functions already covered through the scroll
  behaviour, so the real risk is the clock formatter — zero-padding, and the
  `m:ss` → `h:mm:ss` boundary. Since it cannot be unit-tested here, it is verified
  by hand at three points (`0:07`, `9:59`, `1:00:00`) and those three values are
  named in the PR. Do not add a Node toolchain to a Python package for it.
- Any new test must be Windows-clean. `tests/test_readview_library.py:313` pipes
  source through a `text=True` subprocess and fails on Windows with cp1252 —
  do not copy that pattern.

**Manual, and it belongs in the PR:** a screenshot at iPad-portrait size
(834×1194) showing the cluster in place, and confirmation that pressing it does
not scrub the script.

## Out of scope

- The doubled title, which is #141's other half.
- Any change to scroll behaviour, momentum, the resume mark, or the keep-awake
  path.
- Syncing any of this state between devices — see the shared-read-state design.
