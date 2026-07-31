# Library index: collapsible channels, swipe-to-mark-read, one cut per episode

**Date:** 2026-07-31
**Status:** approved, implementing

The Pi serves a flat list of every read-view that has been pushed. With two
channels and both cuts of some episodes it reached ten entries, and the list
gives no way to tell what has already been read in a session. This adds channel
grouping, a swipe gesture that marks a script read, and cuts the entry count by
shipping one variant per episode instead of both.

## Where the generator lives

The index generator moves out of the `ssh` heredoc in
`~/claude/scripts/coldread-push.sh` into `vo_format/readview/library.py`, and the
push script pipes that file instead of embedding it:

```bash
ssh "$PI" "python3 - $PI_DIR" < "$REPO/vo_format/readview/library.py"
```

Two reasons. The generator grows from ~35 lines of HTML to ~150 once it carries
CSS and a touch handler, and the heredoc it lives in already documents that
brace-dense content gets silently mangled by f-string escaping there. In the
repo it is diffable, committed, and unit-testable.

The invariant that made on-Pi generation the right choice is preserved exactly:
the *file* is piped, the `glob("*.html")` still runs on the Pi, so a
`--channel`-scoped push still cannot drop the other channel's entries from the
page. Generating locally and rsyncing the result would break that, and is
rejected for it.

The module must be **stdlib-only and self-contained** — no relative imports, no
third-party dependencies — because it executes as `python3 -` on the Pi with
nothing installed. Pi runs Python 3.13.5.

### Interface

```python
CHANNEL_LABELS: dict[str, str]   # filename channel -> display label
CHANNEL_ORDER: tuple[str, ...]   # display order; unlisted channels follow

def render_index(entries: Sequence[IndexEntry]) -> str
def main(directory: str) -> None   # glob, build entries, write index.html
```

`IndexEntry` is `(channel, title, filename, date)`. Tests call `render_index`
with fabricated entries, so no Pi and no filesystem are involved.

## Channels

One `<details>`/`<summary>` per channel. Order is Cassette Lore, then Birds of
Play. Display labels come from an explicit map:

```python
CHANNEL_LABELS = {"CassetteLore": "Cassette Lore"}
```

An unmapped channel passes through verbatim and sorts after the known ones, so a
new channel appearing in a filename can never vanish from the page.

Both categories are **closed on first visit**, which means the summary line has
to carry the information the collapsed body hides:

```
Cassette Lore — 4 scripts · 2 read
```

The read count updates live as rows are marked. Open/closed state is persisted
per channel, so "closed by default" applies to the first visit only — returning
from a script via `← Library` does not land on a fully collapsed page.

## Swipe to mark read

- `touchstart` records the start point. `touchmove` takes over only once
  `|dx| > |dy|`, so vertical scrolling of the library is unaffected, and drags
  the row under the finger.
- `touchend` past **−60 px** toggles read state; anything shorter springs back.
- A completed swipe suppresses the row's `click`, so marking a script never
  opens it on the way out.
- Read state renders as a green ✓ with the row dimmed, and moves the `<li>` to
  the end of its `<ul>`.
- Unmark by swiping left again, or by tapping the ✓ (44 px target). Tapping is
  also the mouse-only path, which is what makes the feature verifiable in a
  desktop browser before it reaches the iPad.
- Unmarking returns the row to title order among the unread entries.

## Storage

Keys follow `reader.js`'s `<prefix>:<key>` JSON convention:

| Key | Value |
|---|---|
| `coldread-library:read` | `{filename: true}` |
| `coldread-library:open` | `{channel: bool}` |

One key each rather than a key per script, so pruning is a single pass: on load,
`read` entries whose filename is not in the current index are dropped. Filenames
carry the draft version (`… Blood Ministry v1.14 - batched - readview.html`), so
this is both what stops successive drafts of one episode accumulating forever and
the mechanism by which **a new draft comes back unread** — v11 is not the v10
that was read.

All storage access is wrapped in `try`/`catch`. Losing state is acceptable;
refusing to render is not. This matches `reader.js`.

## One cut per episode

`coldread-push.sh` currently stages **both** variants of every gated episode that
has them. It ships one, chosen per series (the first path segment of the gate
entry):

```bash
declare -A CUT=( [warhammer40k]=batched )   # default: formatted
```

If the wanted cut has no PDF, the run prints a loud `WARNING` and falls back to
`formatted` — visible, never silent. Gate-list syntax is untouched, so
`tools/gate-blockers.py` keeps parsing it unchanged.

Effect on the current gate: 7 read-views where there were 10 — Warcraft ep1,
Bloodborne ep1 and Disco Elysium ep1 formatted, Warhammer 40K ep4 batched, plus
the three Birds of Play scripts.

## Testing

`tests/test_readview_library.py` covers `render_index`: channel grouping, label
mapping, channel ordering, unknown-channel passthrough, per-channel script and
read counts, `details` having no `open` attribute, and HTML escaping (Warcraft's
`Quel'Thalas` apostrophe is a real entry).

The touch handler gets **no automated test**. It is verified in a desktop browser
on the rig via the tap path, then confirmed on the iPad. Stated rather than
implied.

## Out of scope

The read-view marking itself read when it scrolls to the end. The swipe is the
requested gesture; inferring completion from scroll position is a separate
feature with its own failure modes.
