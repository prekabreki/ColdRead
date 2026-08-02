# HUD Speed Cluster and Countdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four scattered words-per-minute controls with one visible stacked pair at the bottom-right, and add a time-remaining countdown to the HUD readout.

**Architecture:** The speed pair becomes an absolutely-positioned child of `#hud`. That placement is load-bearing rather than cosmetic: `#hud` is already `position: fixed` so it is the containing block (the cluster then tracks the HUD's real top edge including `env(safe-area-inset-bottom)` with no height variable to keep in sync), and the touch handler already exempts `#hud` from the freeze-and-drag gesture, so the cluster inherits that exemption instead of needing a new selector. The countdown is derived from the existing `pxPerSecond()` rather than from the word count, so it cannot disagree with what the scroll engine actually does.

**Tech Stack:** Python 3.10+ (f-string HTML template), vanilla ES5-style JS, hand-written CSS, pytest. No build step, no JS test runner, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-02-hud-speed-and-countdown-design.md`

## Global Constraints

- Every touch target keeps `min-width: 44px; min-height: 44px` (already set by the `#hud button` rule — the cluster's buttons are descendants of `#hud`, so they inherit it; do not add a competing rule).
- The rendered page stays fully self-contained: no `<script src>`, no `<link>`, no `@import`, no remote URL, no `url(...)` that is not a `data:` URI. Enforced by existing tests in `tests/test_readview_render.py::TestSelfContainment`.
- Preserve the `bl` class on body-sized lines and `data-title` on `<body>`. `reader.js` probes `.bl` to measure line height and derives its `localStorage` key from `data-title`; both have caused bugs before.
- Never delete or alter a line that came out of the PDF. Only generated chrome may change.
- `ruff` line length is 88 (`pyproject.toml:54`).
- Do NOT add a JS test runner or any Node tooling to this Python package.
- New tests must be Windows-clean. Do not pass source text through a `subprocess.run(..., text=True)` — `tests/test_readview_library.py:313` does and fails under cp1252.
- `pytest` is currently undeclared in `pyproject.toml`; run the suite with the interpreter that already has it (`./.venv/bin/python -m pytest`, or `.\.venv\Scripts\python.exe -m pytest` on Windows).

---

### Task 1: Group the speed controls into one visible cluster

Deletes the two invisible edge strips and the two in-row wpm buttons; emits one stacked pair inside the HUD. All three files change together because the page is broken in between: the CSS and JS both still reference `.zone` until they don't.

**Files:**
- Modify: `vo_format/readview/render.py:110-111` (the two `.zone` divs) and `:118-126` (the HUD block)
- Modify: `vo_format/readview/reader.css:75-93` (delete the `.zone` rules) and add a `#speed` rule
- Modify: `vo_format/readview/reader.js:18-32` (the `el` map), `:366-371` and `:409-412` (the two exemption selectors), `:470-473` (the `holdRepeat` calls)
- Test: `tests/test_readview_render.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the element ids `#speed`, `#wpmdown`, `#wpmup` in the rendered page. Task 2 does not touch them. `holdRepeat(node, fn)` keeps its existing signature.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_readview_render.py`. Put them in a new class after `TestSelfContainment`:

```python
class TestSpeedCluster:
    """The wpm control is one grouped pair, and it lives inside the HUD."""

    def _html(self) -> str:
        return render(_script([_line("A line of body text.")]))

    def test_the_edge_strips_are_gone(self) -> None:
        html = self._html()
        assert 'class="zone"' not in html
        assert ".zone" not in html          # the CSS rules too

    def test_the_cluster_is_inside_the_hud(self) -> None:
        # Region-based, and the region is what matters: outside #hud the page
        # still LOOKS right while pressing the cluster scrubs the script,
        # because the touch handler's exemption is keyed on #hud.
        html = self._html()
        hud_open = html.index('<div id="hud">')
        hud_end = html.index('<video id="awake"')
        hud_block = html[hud_open:hud_end]
        assert '<div id="speed">' in hud_block
        assert 'id="wpmdown"' in hud_block
        assert 'id="wpmup"' in hud_block

    def test_minus_comes_before_plus(self) -> None:
        html = self._html()
        assert html.index('id="wpmdown"') < html.index('id="wpmup"')

    def test_the_buttons_are_not_split_by_the_status_readout(self) -> None:
        html = self._html()
        status = html.index('id="status"')
        assert not (html.index('id="wpmdown"') < status < html.index('id="wpmup"'))

    def test_the_cluster_has_a_css_rule(self) -> None:
        assert "#speed {" in self._html()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_readview_render.py::TestSpeedCluster -v`

Expected: FAIL. `test_the_edge_strips_are_gone`, `test_the_cluster_is_inside_the_hud`, and `test_the_cluster_has_a_css_rule` fail on missing/present markup. `test_the_buttons_are_not_split_by_the_status_readout` fails because today the readout sits between them — that is the current bug, asserted.

- [ ] **Step 3: Remove the edge strips and regroup the HUD in `render.py`**

Delete these two lines entirely (currently `render.py:110-111`):

```python
<div class="zone" id="slower">&minus;</div>
<div class="zone" id="faster">+</div>
```

Replace the whole `#hud` block with this. Note `#speed` comes last and the two wpm buttons move inside it:

```python
<div id="hud">
{back_button}<button id="smaller" type="button" aria-label="Smaller text">A&minus;</button>
<button id="bigger" type="button" aria-label="Larger text">A+</button>
<button id="play" type="button" aria-label="Play or pause">&#9654;</button>
<span id="status"></span>
<button id="theme" type="button" aria-label="Toggle light or dark">&#9790;</button>
<div id="speed">
<button id="wpmdown" type="button" aria-label="Slower">&minus;</button>
<button id="wpmup" type="button" aria-label="Faster">+</button>
</div>
</div>
```

Also update the comment above `library_attr` (`render.py:85-87`), which currently justifies the back button's placement by the `#hud, .zone` exemption. It should now read:

```python
    # The button lives in #hud on purpose. The touch handler exempts #hud from
    # the freeze-and-drag gesture, so a control placed anywhere else would scrub
    # the script under the finger on its way out. #speed rides on the same
    # exemption, which is why it is a child of #hud rather than a sibling.
```

- [ ] **Step 4: Replace the `.zone` CSS with the `#speed` rule**

In `reader.css`, delete the entire block from the `/* Speed zones: ... */` comment through `#faster { right: 0; }` (currently lines 75-93). Put this in its place:

```css
/* The speed pair: grouped, visible, and thumb-reachable at the bottom-right.
   An absolutely-positioned child of #hud, which is doing three jobs at once.
   #hud is position:fixed, so it is the containing block and the cluster tracks
   the HUD's real top edge including the safe-area inset in its padding — a
   --hud-height variable would have to be kept in sync by hand and would be
   wrong on any device with a different inset. The touch handler exempts #hud
   from the freeze-and-drag gesture, so being a child inherits that exemption;
   anywhere else, a press would scrub the script underneath. And #hud is
   display:flex, so an out-of-flow child leaves the HUD row's layout alone.

   Solid --bg rather than a translucent fill: this sits over moving text, and a
   control you read through is a control you misread. The buttons inherit size,
   radius and fill from the #hud button rule. */
#speed {
  position: absolute;
  right: 12px;
  bottom: calc(100% + 12px);
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--bg);
  border-radius: 8px;
}
```

- [ ] **Step 5: Drop the strip references from `reader.js`**

Three edits, all deletions or one-word changes.

In the `el` map, delete these two entries:

```js
    slower: document.getElementById("slower"),
    faster: document.getElementById("faster"),
```

In both exemption checks, drop `, .zone` — the `touchstart` handler:

```js
    if (e.target.closest("#hud")) { return; }
```

and the `mousedown` handler:

```js
    if (touchSeen || e.target.closest("#hud")) { return; }
```

Delete these two `holdRepeat` calls, keeping the `wpmdown`/`wpmup` pair below them:

```js
  holdRepeat(el.slower, function () { nudgeWpm(-WPM_STEP); });
  holdRepeat(el.faster, function () { nudgeWpm(WPM_STEP); });
```

- [ ] **Step 6: Run the tests**

Run: `./.venv/bin/python -m pytest tests/test_readview_render.py -v`

Expected: PASS, including the pre-existing `TestSelfContainment` and structure tests.

Then the whole suite, because `render()` output is asserted from more than one file:

Run: `./.venv/bin/python -m pytest tests/ -q`
Expected: all pass except the pre-existing Windows-only cp1252 failure in `tests/test_readview_library.py`, which is not this task's.

Run: `./.venv/bin/ruff check vo_format tests`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add vo_format/readview/render.py vo_format/readview/reader.css \
        vo_format/readview/reader.js tests/test_readview_render.py
git commit -m "Group the wpm controls into one cluster inside the HUD"
```

---

### Task 2: Add the time-remaining countdown

**Files:**
- Modify: `vo_format/readview/reader.js` — add two helpers, extend `paintHud()` (currently `:106-114`)
- Modify: `vo_format/readview/reader.css:123` — widen `#status`
- Test: `tests/test_readview_render.py`

**Interfaces:**
- Consumes: `maxScroll()`, `pxPerSecond()`, `pos`, `wpm`, `percent()` — all already defined in `reader.js`.
- Produces: `clockText(seconds) -> string` and `secondsLeft() -> number`. Nothing later consumes them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_readview_render.py`:

```python
class TestCountdown:
    """The HUD says how long is left, derived from the scroll engine."""

    def _html(self) -> str:
        return render(_script([_line("A line of body text.")]))

    def test_the_countdown_helpers_are_inlined(self) -> None:
        html = self._html()
        assert "function clockText(" in html
        assert "function secondsLeft(" in html

    def test_it_is_derived_from_the_scroll_speed_not_the_word_count(self) -> None:
        # The whole point: one source of truth with the autoscroll. A
        # word-count estimate is a parallel calculation that can drift from
        # the thing actually moving the page.
        html = self._html()
        assert "(maxScroll() - pos) / pxPerSecond()" in html

    def test_the_readout_includes_the_clock(self) -> None:
        assert 'clockText(secondsLeft())' in self._html()

    def test_the_status_field_is_wide_enough_for_it(self) -> None:
        # 15ch fitted "42% · 150 wpm"; the clock needs more or the HUD jitters
        # as the digits change.
        html = self._html()
        assert re.search(r"#status\s*\{[^}]*min-width:\s*22ch", html)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_readview_render.py::TestCountdown -v`
Expected: FAIL, all four, on missing content.

- [ ] **Step 3: Add the two helpers to `reader.js`**

Insert directly above `function paintHud()`:

```js
  // Remaining time, derived from the scroll engine rather than from the word
  // count on <body>. This IS the time until autoscroll reaches the end, by
  // construction, so it cannot drift from what the page does; it needs no
  // attribute an older read-view might lack; and it follows any future change
  // to pxPerSecond() with no second edit. pxPerSecond() is always positive
  // (WPM_MIN is 40 and lineHeightPx() has a non-zero fallback), so there is no
  // divide-by-zero to guard.
  function secondsLeft() {
    var px = maxScroll() - pos;
    return px <= 0 ? 0 : px / pxPerSecond();
  }

  function clockText(seconds) {
    if (!isFinite(seconds) || seconds < 0) { seconds = 0; }
    var total = Math.round(seconds);
    var s = total % 60;
    var m = Math.floor(total / 60) % 60;
    var h = Math.floor(total / 3600);
    var ss = (s < 10 ? "0" : "") + s;
    if (h > 0) { return h + ":" + (m < 10 ? "0" : "") + m + ":" + ss; }
    return m + ":" + ss;
  }
```

- [ ] **Step 4: Put it in the readout**

Change the one line in `paintHud()`:

```js
    el.status.textContent = done + "% · " + wpm + " wpm · " +
      clockText(secondsLeft()) + (running ? "" : " ▌▌");
```

Keep the rest of `paintHud()` exactly as it is — in particular the
`el.hud.style.setProperty("--progress", …)` line and its comment about not
setting the custom property on `documentElement`.

Because this lives in `paintHud()`, the countdown repaints on every scroll frame,
every wpm nudge, and every `A±`. The last of those matters: changing type size
changes `scrollHeight`, so the estimate moves even though `pos` did not.

- [ ] **Step 5: Widen the status field**

In `reader.css`, change the `#status` rule:

```css
#status { min-width: 22ch; text-align: center; font-variant-numeric: tabular-nums; }
```

`min-width` is a floor, not a clamp — at the extremes (`13% · 40 wpm · 1:15:00 ▌▌`)
the text simply takes the room it needs. The HUD can afford it either way: this
change removed two 44px buttons plus their gaps from the row, so it is strictly
less crowded than before.

- [ ] **Step 6: Run the tests**

Run: `./.venv/bin/python -m pytest tests/ -q`
Expected: pass, except the pre-existing Windows cp1252 failure.

Run: `./.venv/bin/ruff check vo_format tests`
Expected: clean.

- [ ] **Step 7: Note the formatter cases for the reviewer — do not run them yourself**

There is no JS test runner here and this task does not add one, so the three
values that exercise zero-padding and the hour boundary cannot be checked from
the worktree:

```js
clockText(7)      // expect "0:07"
clockText(599)    // expect "9:59"
clockText(3600)   // expect "1:00:00"
```

**List these three expectations in the PR description as unverified.** Do not
attempt to run them — a browser is not available in an executor worktree, and
reaching outside it for one is how a session gets killed with the work lost.
Confirming them is the reviewer's gate.

- [ ] **Step 8: Commit**

```bash
git add vo_format/readview/reader.js vo_format/readview/reader.css \
        tests/test_readview_render.py
git commit -m "Show time remaining in the HUD, derived from the scroll speed"
```

---

### Task 3: Correct the README and confirm on device

**Files:**
- Modify: `README.md` — the `**Controls:**` paragraph (around line 106) and the HUD sentence after it

**Interfaces:**
- Consumes: the shipped behaviour from Tasks 1 and 2.
- Produces: nothing consumed by code.

- [ ] **Step 1: Fix the two false sentences**

`README.md` currently says:

> The unlabelled left and right edge strips step the speed in words per minute.

That is now false — the strips are gone. And:

> The HUD shows how far through the script you are, as a percentage beside the wpm and as a fill along its top edge.

That is now incomplete. Replace the first with a description of the cluster and
extend the second to mention the countdown. Keep the surrounding voice: plain
prose, short paragraphs, no marketing tone, no emoji.

Do not restructure the section, do not add a new `##` heading, and do not touch
anything else in it. Issue #144 is editing the same section to document the
library and has been constrained away from this paragraph; staying inside these
two sentences is what keeps the two changes from colliding.

- [ ] **Step 2: Verify no other README claim went stale**

Run: `grep -n -i "strip\|edge\|zone\|wpm\|percentage" README.md`

Read each hit and confirm it still matches the shipped page. Fix any that do not.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Correct the README's read-view controls after the HUD regroup"
```

---

## Human review gate

These cannot be checked from a worktree and are deliberately not any task's
steps. An implementer that reaches outside the repo for a browser or a device
risks losing the whole session's work for a check it was never meant to run.

- [ ] On a served read-view at iPad portrait size (834×1194): the cluster sits
      bottom-right above the HUD, `−` above `+`, both comfortably tappable.
- [ ] **Pressing and holding either button does not move the script.** The one
      regression no DOM test can see: a cluster that ended up outside `#hud`
      renders correctly and scrubs the script under your finger.
- [ ] Holding `+` ramps the wpm rather than stepping once.
- [ ] The countdown falls as the script scrolls, and jumps when wpm changes.
- [ ] `clockText(7)` → `0:07`, `clockText(599)` → `9:59`, `clockText(3600)` →
      `1:00:00` in the browser console.

---

## Self-review

**Spec coverage.** Cluster placement inside `#hud` and the three reasons — Task 1
Steps 3-4. Strips deleted — Task 1 Steps 3-5. `−` above `+` — Task 1 Step 3, with
a test. Touch exemption preserved — Task 1 Step 5, verified manually in Task 3
Step 3 because no DOM test can see it. Countdown derived from `pxPerSecond()` —
Task 2 Step 3, with a test asserting the exact expression. `m:ss` / `h:mm:ss`,
shown while paused, repainted on `A±` — Task 2 Steps 3-4. `#status` at 22ch —
Task 2 Step 5. `0:00` for a short script — covered by `secondsLeft()`'s `px <= 0`
branch. README ownership — Task 3. The spec's "eyes-free loss" note needs no task;
it is a recorded trade, and the keyboard bindings it points at already exist.

**Placeholders.** None. Every code step carries the literal text to write.

**Type consistency.** `clockText` and `secondsLeft` are named identically in the
test assertions (Task 2 Step 1), the implementation (Step 3), the call site
(Step 4) and the manual check (Step 7). `#speed`, `#wpmdown`, `#wpmup` are
consistent across Task 1's tests, template and CSS.
