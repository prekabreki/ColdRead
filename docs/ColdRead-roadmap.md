# ColdRead — Feature Roadmap

Three tracks, ordered by how much they're worth relative to effort. Track 1 is
foundations; 2 and 3 are the things that make the tool worth reaching for
instead of formatting by hand.

---

## Track 1 — Foundations

These are the gaps a user hits in the first hour. None are glamorous; all of
them erode trust when missing.

### 1.1 Round-trip verification

The README's central promise is *"Claude classifies; it never rewrites."* Right
now that's an architectural claim. Make it a test.

After formatting, extract the text back out of the rendered PDF, normalise
whitespace and typographic substitutions, and assert token-level equality
against the source. Any divergence is a hard failure, not a warning.

- Ship it as a `--verify` flag, default on.
- Add it to the pytest suite across all five sample archetypes.
- Report divergences as a diff, not a boolean.

This is also the single strongest thing to put in the README. "Verified
lossless" is a claim competitors won't make.

### 1.2 Input formats

Current: `.md`, `.txt`, `.pdf`, `.docx`. Worth adding:

| Format | Why |
| --- | --- |
| `.fountain` | Free screenplay standard, plain-text, trivial to parse. Direct overlap with your audience. |
| `.fdx` | Final Draft XML. Ugly but it's what studios send. |
| `.rtf` | Still the default export from a surprising number of tools. |
| `.srt` / `.vtt` | Dub and ADR scripts arrive as subtitle files. |

Fountain first — it's a weekend of work and it's the format people who
*already* care about script formatting use.

### 1.3 Output formats

PDF is the read target, but the formatted structure has value elsewhere.

- **HTML** — shareable, and the substrate for prompter mode (Track 3).
- **DOCX / plain text** — for the person who needs to edit and send back.
- **JSON sidecar** — the parsed structure: speakers, line IDs, breath groups,
  pronunciation entries. This is the integration surface for everything else.

The sidecar matters more than it sounds. Once it exists, other tools can
consume ColdRead's analysis without reimplementing it.

### 1.4 Document furniture

Standard things a formatted script has that ColdRead doesn't:

- Header: title, revision date, `page X of Y`.
- Auto-generated cast page — every speaker, line count, word count, first
  appearance page.
- Widow/orphan control: never leave a speaker cue stranded at a page bottom,
  never break a breath group across pages.
- `(MORE)` / `(CONT'D)` conventions when a speech does span a page break.
- Revision marks: on re-import of a changed script, asterisk changed lines in
  the margin and mark the page. Standard practice, and it's what makes the tool
  usable across a script's life rather than once.

### 1.5 Typography and encoding

- Full Unicode pass: smart quotes, en/em dashes, ellipses, combining
  diacritics. Icelandic þ/ð/æ/ö and Nordic vowels break naive font-fallback
  chains — test explicitly, and fail loudly rather than rendering tofu.
- Font size, margins, and leading as first-class settings, not derived
  constants. Some readers need 18pt; some studios need specific margins.
- Verify the speaker palette against actual grayscale conversion, not assumed
  lightness values. Run the check in CI.

### 1.6 Failure behaviour

- AI pass times out, returns malformed JSON, or hits a rate limit → fall back
  to archetype defaults, format anyway, note the degradation in the GUI. Never
  block on the network.
- Cache analysis results keyed by source hash, so a re-run of an unchanged
  script costs nothing.
- Settings persist per project, not globally. A drama and a documentary want
  different defaults and shouldn't overwrite each other.

---

## Track 2 — Recording Session Features

The formatting is the pre-session step. These cover the session itself, which
is where the tool stops being a one-shot utility.

### 2.1 Timing estimates

Per-section and total runtime, printed in the margin.

- Configurable words-per-minute, with a sensible default per archetype
  (narration reads slower than dialogue).
- Account for pause weight: a breath group break, a paragraph break, and a
  scene break imply different silences. Model them as configurable durations.
- Running cumulative timecode down the margin, so a reader knows where they are
  against a target duration.
- Flag sections that exceed a target: "this scene runs 0:47 against a 0:30
  slot."

For anyone cutting to picture or filling an ad slot, this is the feature that
justifies the tool by itself.

### 2.2 Line IDs and slating

Number every line or breath group, printed in the gutter.

- Stable IDs that survive reformatting — derived from content, not position, so
  a script edit doesn't renumber everything downstream.
- Speak-aloud slate format: "line forty-two, take three."
- The IDs go in the JSON sidecar, which is what makes 2.3 possible.

### 2.3 Take log

A lightweight session companion, either in the GUI or as a separate pane.

- Mark a line as recorded, flagged, or needs-pickup, with a note.
- Export a pickup list: only the lines that need re-recording, formatted as its
  own short PDF. This is a genuinely tedious manual job today.
- Export the log as CSV for whoever's editing.

### 2.4 Pronunciation lexicon

Currently pronunciation hints are per-run. Make them persistent.

- A project-level lexicon: term → IPA + respelling ("KAY-oss"), with an
  optional note on origin or context.
- Once you correct a name, it stays corrected across every script in the
  project. Recurring character names in a series are the obvious case.
- Show both IPA and respelling. Readers split on which they parse faster, and
  the respelling is what someone glances at mid-take.
- Import/export as JSON, so a lexicon can be shared with a co-narrator or
  handed to a client.

### 2.5 Session artefacts

Small exports that save real time:

- A cast/character sheet for a multi-voice session.
- A per-speaker script: only one character's lines, with cues, for sending to a
  remote performer.
- Word and line counts per speaker, for rate negotiation.

---

## Track 3 — Prompter Mode

A second render target from the same layout engine. Instead of a paginated PDF,
a scrolling read view.

### 3.1 Core

- HTML output, self-contained single file, opens in any browser. No install, no
  second app.
- Auto-scroll at a configurable rate, derived from the same WPM model as the
  timing estimates so the two agree.
- Space bar / arrow keys for start, stop, speed up, slow down. Foot pedal
  support comes free if pedals are configured as keyboards, which most are.
- Large type, high contrast, generous leading. Dark mode as the default — this
  is a booth, not a page.

### 3.2 Read-specific behaviour

- Highlight the active breath group; dim the rest. The eye needs an anchor.
- A fixed read line at eye height, with text scrolling to meet it, rather than
  the whole block sliding.
- Pronunciation hints inline on hover or persistently above the term, since
  there's no margin to put them in.
- Mirror mode for teleprompter glass.
- Elapsed and remaining time, against the target duration.

### 3.3 Integration

- Line IDs visible, so a flagged take in the prompter maps to the same ID in
  the PDF and the take log.
- Tapping a line marks it for pickup — feeding Track 2's take log directly.

That last connection is what ties the three tracks together: one parse, one set
of line IDs, three surfaces (PDF, prompter, log) reading from the same JSON.

---

## Suggested Order

1. Round-trip verification (1.1) — cheap, and it upgrades the README's core
   claim from assertion to fact.
2. JSON sidecar (1.3) — everything downstream depends on it.
3. Line IDs (2.2) — small, and unlocks the prompter and log integrations.
4. Timing estimates (2.1) — highest visible value per line of code.
5. Document furniture (1.4) — unglamorous, but its absence is what makes a tool
   feel unfinished.
6. Prompter mode core (3.1) — a large, self-contained chunk. Do it once the
   sidecar and line IDs are stable.
7. Fountain input (1.2) and the persistent lexicon (2.4) — parallel, independent.

Items 1–3 are foundation work that makes 4–7 much cheaper. Doing them in the
other order means building the session and prompter features twice.
