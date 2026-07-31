"""Render the library index that lists every read-view in a directory.

This is the only navigation a tablet in a booth has, so an entry that fails to
appear is a script that cannot be reached. Everything here is presentation; the
set of read-views is whatever is on disk.

STDLIB ONLY, AND NO PACKAGE IMPORTS. This module is designed to be piped to a
remote interpreter and run as `python3 - <directory>`:

    ssh pi "python3 - coldread-library" < vo_format/readview/library.py

The publisher does that rather than generating the page locally and copying it,
because the index must describe what is *actually* on the far end: a push scoped
to one channel stages only that channel, and an index built from the staging
directory would silently drop the other channel's entries. Adding an import from
`vo_format` here would break that invocation, which is why a test asserts there
are none.
"""

from __future__ import annotations

import datetime
import html
import pathlib
import re
import sys
from typing import NamedTuple, Sequence

#: Display labels for channels, keyed by the prefix the filename carries. An
#: unlisted channel is shown verbatim rather than dropped — a new channel must
#: never be able to vanish from the only navigation there is.
CHANNEL_LABELS = {"CassetteLore": "Cassette Lore"}

#: Display order. Channels absent from this list follow, alphabetically.
CHANNEL_ORDER = ("CassetteLore", "Birds of Play")

#: Where a read-view lands when its filename carries no "Channel — " prefix.
UNFILED = "Unfiled"

_SEPARATOR = " — "
_SUFFIX = " - readview.html"

#: `render.py` stamps the word count on <body>. Reading it back is the only way
#: the index can show length: the filename does not carry it, and inventing a
#: second place to record it would give the two ends something to drift on.
_WORDS_RE = re.compile(r'data-words="(\d+)"')

#: How much of a read-view to read looking for the count. The attribute sits on
#: <body>, currently ~4.3KB in, behind the inlined CSS; everything that scales
#: with the script — the lines themselves — comes after it, which is what makes
#: a bounded read worth having on a memory-tight Pi. A bounded read also means a
#: truncated or half-written file cannot break the index. The headroom over the
#: real offset is deliberate and pinned by a test: the CSS grows ahead of the
#: attribute, and a prefix that fell short would show no lengths at all while
#: every row still rendered perfectly.
_PREFIX_BYTES = 16384


class IndexEntry(NamedTuple):
    channel: str  # the raw prefix from the filename, not the display label
    title: str
    filename: str
    date: str  # ISO date the read-view was derived
    words: int | None = None  # None when the read-view carries no count


def words_in(path: pathlib.Path) -> int | None:
    """Read the word count `render.py` stamped on this read-view's <body>.

    Returns None for anything unexpected — an older read-view, a file being
    written as we look at it, something that is not a read-view at all. A row
    without a length is worth far more than an index that fails to render.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(_PREFIX_BYTES)
    except OSError:
        return None
    match = _WORDS_RE.search(head.decode("utf-8", "replace"))
    return int(match.group(1)) if match else None


def entry_for(path: pathlib.Path) -> IndexEntry:
    """Describe one read-view file.

    Channel and title come from the filename because that is the only thing the
    publisher and the far end agree on; no path→title mapping exists to drift.
    """
    channel, separator, rest = path.name.partition(_SEPARATOR)
    if not separator:
        # Hand-placed read-views have no channel prefix. They still get a row.
        channel, rest = UNFILED, path.name
    if rest.endswith(_SUFFIX):
        rest = rest[: -len(_SUFFIX)]
    when = datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()
    return IndexEntry(
        channel=channel,
        title=rest,
        filename=path.name,
        date=when,
        words=words_in(path),
    )


def _grouped(entries: Sequence[IndexEntry]) -> list[tuple[str, list[IndexEntry]]]:
    by_channel: dict[str, list[IndexEntry]] = {}
    for entry in entries:
        by_channel.setdefault(entry.channel, []).append(entry)

    def rank(channel: str) -> tuple[int, str]:
        if channel in CHANNEL_ORDER:
            return (CHANNEL_ORDER.index(channel), "")
        return (len(CHANNEL_ORDER), channel)

    return [
        (channel, sorted(by_channel[channel], key=lambda e: e.title.lower()))
        for channel in sorted(by_channel, key=rank)
    ]


def _row(entry: IndexEntry, position: int) -> str:
    # data-key is the filename, which carries the draft version. That is what
    # makes read state reset when a new draft is published: v11 is not the v10
    # that was read. data-i is the row's place in title order, so unmarking can
    # put it back where it belongs instead of leaving it at the bottom.
    key = html.escape(entry.filename)
    # Length leads the sub-line: it is what decides whether a script fits the
    # time available, which is the question the library page gets asked.
    facts = [] if entry.words is None else [f"{entry.words:,} words"]
    facts.append(html.escape(entry.date))
    return (
        f'<li data-key="{key}" data-i="{position}"><div class="row">'
        f'<a href="{key}"><b>{html.escape(entry.title)}</b>'
        f'<span>{" &middot; ".join(facts)}</span></a>'
        '<button class="check" type="button" aria-label="Mark read or unread"'
        ">&#10003;</button></div></li>"
    )


def _section(channel: str, entries: Sequence[IndexEntry]) -> str:
    label = html.escape(CHANNEL_LABELS.get(channel, channel))
    noun = "script" if len(entries) == 1 else "scripts"
    rows = "\n".join(_row(entry, i) for i, entry in enumerate(entries))
    # Both sections start closed, so the summary has to carry what the collapsed
    # body hides — hence the count here and the read tally filled in by script.
    return (
        f'<details data-channel="{html.escape(channel)}">'
        f"<summary>{label}<span class=\"meta\">{len(entries)} {noun}"
        '<span class="readcount"></span></span></summary>\n'
        f"<ul>\n{rows}\n</ul></details>"
    )


_CSS = """
body{margin:0;padding:24px;background:#121212;color:#e8e6e3;
font-family:"Courier New",monospace;-webkit-text-size-adjust:100%}
header{display:flex;align-items:center;justify-content:space-between;
gap:12px;margin:0 0 6px}
h1{font-size:20px;opacity:.6;font-weight:400;margin:0}
#loaded{opacity:.35;font-size:13px;margin:0 0 18px;
transition:opacity .2s,color .2s}
#loaded.flash{opacity:1;color:#8fd39b}
#refresh{font:inherit;font-size:21px;color:#e8e6e3;background:#1c1c1c;
border:0;border-radius:8px;min-width:44px;min-height:44px;
-webkit-tap-highlight-color:transparent}
#refresh:active{background:#2a2a2a}
#refresh.spin{animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
details{margin:0 0 14px;background:#1a1a1a;border-radius:10px;overflow:hidden}
summary{display:flex;align-items:center;gap:10px;padding:16px 18px;
font-size:17px;cursor:pointer;list-style:none;min-height:44px;
-webkit-tap-highlight-color:transparent}
summary::-webkit-details-marker{display:none}
summary::after{content:"\\25b8";opacity:.45;transition:transform .15s}
details[open] summary::after{transform:rotate(90deg)}
summary .meta{margin-left:auto;opacity:.45;font-size:14px}
ul{list-style:none;padding:0 10px 10px;margin:0}
/* The green sits UNDER the row: sliding left reveals what the gesture means. */
li{margin:0 0 8px;border-radius:10px;overflow:hidden;background:#1d3324}
.row{display:flex;align-items:stretch;background:#1c1c1c;border-radius:10px}
.row a{flex:1;min-width:0;display:block;padding:16px 8px 16px 18px;
color:#e8e6e3;text-decoration:none;font-size:16px;line-height:1.4;
-webkit-tap-highlight-color:transparent}
.row a:active{background:#2a2a2a}
b{display:block;font-weight:700;overflow-wrap:anywhere}
.row a span{opacity:.45;font-size:13px}
.check{flex:0 0 auto;width:48px;border:0;background:transparent;color:#8fd39b;
font:inherit;font-size:20px;opacity:.22;
-webkit-tap-highlight-color:transparent}
li.read .check{opacity:1}
li.read .row{background:#16211a}
li.read .row a{opacity:.42}
"""

# Kept OUT of any f-string. JS is brace-dense, and every brace would need
# doubling — which is exactly how this page's CSS used to get silently mangled
# back when the whole generator lived in a shell heredoc.
_JS = """
(function () {
  var PREFIX = "coldread-library";
  // Losing state is acceptable; refusing to render is not. Same contract as
  // reader.js, which shares this key shape.
  var store = {
    get: function (key, fallback) {
      try {
        var raw = localStorage.getItem(PREFIX + ":" + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (e) { return fallback; }
    },
    set: function (key, value) {
      try { localStorage.setItem(PREFIX + ":" + key, JSON.stringify(value)); }
      catch (e) { /* in-memory only */ }
    }
  };

  var rows = [].slice.call(document.querySelectorAll("li[data-key]"));
  var sections = [].slice.call(document.querySelectorAll("details[data-channel]"));

  // --- read state ----------------------------------------------------------
  // Pruned against what is actually served. Filenames carry the draft version,
  // so without this every superseded draft would sit in storage forever.
  var stored = store.get("read", {}) || {};
  var read = {};
  rows.forEach(function (li) {
    if (stored[li.dataset.key]) { read[li.dataset.key] = true; }
  });
  store.set("read", read);

  function isRead(li) { return !!read[li.dataset.key]; }

  function place(li) {
    var ul = li.parentNode;
    li.classList.toggle("read", isRead(li));
    if (isRead(li)) { ul.appendChild(li); return; }
    // Back into title order, ahead of the read block at the bottom.
    var i = +li.dataset.i;
    var siblings = [].slice.call(ul.children);
    var before = null;
    for (var n = 0; n < siblings.length; n++) {
      var other = siblings[n];
      if (other === li) { continue; }
      if (isRead(other) || +other.dataset.i > i) { before = other; break; }
    }
    ul.insertBefore(li, before);
  }

  function refreshCounts() {
    sections.forEach(function (section) {
      var n = 0;
      [].forEach.call(section.querySelectorAll("li[data-key]"), function (li) {
        if (isRead(li)) { n += 1; }
      });
      section.querySelector(".readcount").textContent =
        n ? " \\u00b7 " + n + " read" : "";
    });
  }

  function toggle(li) {
    if (isRead(li)) { delete read[li.dataset.key]; }
    else { read[li.dataset.key] = true; }
    store.set("read", read);
    place(li);
    refreshCounts();
  }

  // --- swipe left to mark read ---------------------------------------------
  var SWIPE = -60;   // px of travel that commits
  var SLACK = 10;    // px before the gesture is claimed for an axis

  rows.forEach(function (li) {
    var row = li.querySelector(".row");
    var x0 = 0, y0 = 0, dx = 0, axis = null, moved = false;

    li.addEventListener("touchstart", function (e) {
      if (e.touches.length !== 1) { return; }
      x0 = e.touches[0].clientX;
      y0 = e.touches[0].clientY;
      dx = 0; axis = null; moved = false;
      row.style.transition = "none";
    }, { passive: true });

    li.addEventListener("touchmove", function (e) {
      if (axis === "y" || e.touches.length !== 1) { return; }
      var ddx = e.touches[0].clientX - x0;
      var ddy = e.touches[0].clientY - y0;
      if (!axis) {
        if (Math.abs(ddx) < SLACK && Math.abs(ddy) < SLACK) { return; }
        // A vertical drag is the page scrolling. Claiming it would make the
        // library unscrollable, so hand it back for the rest of the gesture.
        axis = Math.abs(ddx) > Math.abs(ddy) ? "x" : "y";
        if (axis === "y") { return; }
      }
      dx = Math.max(-110, Math.min(0, ddx));
      row.style.transform = "translateX(" + dx + "px)";
      moved = true;
      e.preventDefault();
    }, { passive: false });

    li.addEventListener("touchend", function () {
      row.style.transition = "transform .18s ease-out";
      row.style.transform = "";
      if (axis === "x" && dx <= SWIPE) { toggle(li); }
      axis = null;
    });

    // A swipe must not open the script on its way out.
    li.querySelector("a").addEventListener("click", function (e) {
      if (moved) { e.preventDefault(); moved = false; }
    });

    // The tap path: an explicit control, and the only way to do this without a
    // touchscreen — which is what makes the feature checkable in a browser.
    li.querySelector(".check").addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      toggle(li);
    });
  });

  // --- collapsed by default, then remembered -------------------------------
  var open = store.get("open", {}) || {};
  sections.forEach(function (section) {
    var channel = section.dataset.channel;
    if (open[channel]) { section.open = true; }
    section.addEventListener("toggle", function () {
      open[channel] = section.open;
      store.set("open", open);
    });
  });

  rows.forEach(place);
  refreshCounts();

  // --- reload ---------------------------------------------------------------
  // The reload works fine but is invisible: the page comes back in ~90ms
  // looking byte-identical, so the button read as a no-op. The timestamp is the
  // one thing that visibly changes. Navigating with ?r= rather than reloading
  // is a workaround for `python3 -m http.server` sending no Cache-Control.
  var loaded = document.getElementById("loaded");
  var fresh = /[?&]r=/.test(location.search);
  loaded.textContent = (fresh ? "reloaded " : "loaded ") +
    new Date().toLocaleTimeString();
  if (fresh) {
    loaded.className = "flash";
    setTimeout(function () { loaded.className = ""; }, 2500);
  }
  document.getElementById("refresh").addEventListener("click", function () {
    this.className = "spin";
    loaded.textContent = "reloading\\u2026";
    location.replace(location.pathname + "?r=" + Date.now());
  });
}());
"""


def render_index(entries: Sequence[IndexEntry]) -> str:
    """Render the library page listing `entries`, grouped by channel."""
    sections = "\n".join(
        _section(channel, group) for channel, group in _grouped(entries)
    )
    noun = "script" if len(entries) == 1 else "scripts"
    return (
        "<!doctype html>\n<html lang=\"en\"><head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
        '<meta name="theme-color" content="#121212">\n'
        "<title>ColdRead</title>\n<style>"
        + _CSS
        + "</style>\n</head>\n<body>\n<header>"
        + f"<h1>ColdRead &mdash; {len(entries)} {noun}</h1>"
        + '<button id="refresh" type="button" aria-label="Reload library"'
        ">&#10227;</button></header>\n"
        '<div id="loaded"></div>\n'
        + sections
        + "\n<script>"
        + _JS
        + "</script>\n</body></html>\n"
    )


def main(directory: str) -> None:
    """Write `directory/index.html` describing the read-views in it."""
    root = pathlib.Path(directory)
    entries = [
        entry_for(path)
        for path in sorted(root.glob("*.html"))
        if path.name != "index.html"
    ]
    (root / "index.html").write_text(render_index(entries), encoding="utf-8")
    print(f"    index: {len(entries)} entries")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: library.py <directory>", file=sys.stderr)
        raise SystemExit(64)
    main(sys.argv[1])
