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

Two consequences of being piped, both visible below. There is no `__file__`, so
every asset this page needs is an inline string literal rather than a sibling
file read at render time — including `_SYNC_JS`, which is a copy of
`readview/sync.js` that a parity test pins byte-for-byte. And the channel names
are not here at all: they arrive from a `channels.json` sitting in the directory
being indexed, so they travel with the content instead of living in this repo.
"""

from __future__ import annotations

import datetime
import html
import json
import pathlib
import re
import sys
from typing import NamedTuple, Sequence

#: Where a read-view lands when its filename carries no "Channel — " prefix.
UNFILED = "Unfiled"

_SEPARATOR = " — "
_SUFFIX = " - readview.html"

#: Optional per-deployment config, read from the directory being indexed so it
#: travels with the content on the same rsync. Absent or malformed means "no
#: opinion": channels then show their raw filename prefix in alphabetical order,
#: which is what this module already did correctly for an unlisted channel.
#: Channel names describe a deployment, not this tool, so they live in a file
#: beside the scripts rather than as literals in a public repo.
_CONFIG_NAME = "channels.json"

#: `render.py` stamps the word count on <body>. Reading it back is the only way
#: the index can show length: the filename does not carry it, and inventing a
#: second place to record it would give the two ends something to drift on.
_WORDS_RE = re.compile(r'data-words="(\d+)"')

# Duplicated from render.py's _VARIANT_RE because library.py is stdlib-only and
# is piped to a remote interpreter, so it cannot import from the package. The
# default "formatted" variant is bookkeeping noise; "batched" carries information.
_FORMATTED_RE = re.compile(r"\s+-\s+formatted$", re.IGNORECASE)

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


def read_channel_config(directory) -> tuple[tuple[str, ...], dict]:
    """Read `channels.json` from `directory`: display order, display labels.

    Returns `((), {})` for anything unusable — absent, unreadable, not JSON, or
    JSON that is not an object. It never raises, and that is the point: a
    hand-edited config with one stray brace must cost a nicer heading, never the
    whole index, because this page is the only navigation there is. Falling back
    lands on behaviour that already worked: raw prefixes, alphabetical.
    """
    try:
        raw = (pathlib.Path(directory) / _CONFIG_NAME).read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError):
        return (), {}
    if not isinstance(parsed, dict):
        return (), {}
    order = parsed.get("order")
    labels = parsed.get("labels")
    return (
        tuple(str(c) for c in order) if isinstance(order, list) else (),
        {str(k): str(v) for k, v in labels.items()}
        if isinstance(labels, dict)
        else {},
    )


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


def _grouped(
    entries: Sequence[IndexEntry], order: Sequence[str]
) -> list[tuple[str, list[IndexEntry]]]:
    by_channel: dict[str, list[IndexEntry]] = {}
    for entry in entries:
        by_channel.setdefault(entry.channel, []).append(entry)

    def rank(channel: str) -> tuple[int, str]:
        if channel in order:
            return (order.index(channel), "")
        return (len(order), channel)

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
    display = _FORMATTED_RE.sub("", entry.title)
    return (
        f'<li data-key="{key}" data-i="{position}"><div class="row">'
        f'<a href="{key}"><b>{html.escape(display)}</b>'
        f'<span>{" &middot; ".join(facts)}</span></a>'
        '<button class="check" type="button" aria-label="Mark read or unread"'
        ">&#10003;</button></div></li>"
    )


def _section(
    channel: str, entries: Sequence[IndexEntry], labels: dict[str, str]
) -> str:
    # An unlabelled channel shows its raw prefix. A new channel must never be
    # able to vanish from the only navigation there is just because nobody has
    # added it to `channels.json` yet.
    label = html.escape(labels.get(channel, channel))
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

# The identical twin of `readview/sync.js`, embedded because this module is
# piped to a remote interpreter and so has no sibling to read. A parity test
# asserts the two match byte for byte; edit both or neither.
#
# Kept OUT of any f-string, for the same reason `_JS` is — see the note there.
_SYNC_JS = r'''"use strict";
// Shared read state, offline first. localStorage is the truth the page renders
// from; the server is where devices meet. Losing state is acceptable, refusing
// to render is not — the same contract reader.js and library.py already keep.
//
// This file has an identical twin: an inline copy inside library.py, which is
// piped to a remote interpreter and therefore cannot read a sibling asset. A
// test asserts the two are byte-identical. Edit both or neither.
function coldreadSync(prefix, href) {
  var FLUSH_MS = 1000, RETRY_MS = 15000;
  var queueKey = prefix + ":pending";
  var listeners = [];
  var timer = null, retry = null, inflight = false;

  function raw(key, fallback) {
    try {
      var value = localStorage.getItem(key);
      return value === null ? fallback : JSON.parse(value);
    } catch (e) { return fallback; }
  }

  function put(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch (e) { /* in-memory only */ }
  }

  // Sync is inert off http(s): a lone read-view opened as a file keeps working,
  // silently local, and never issues a request its deployment did not ask for.
  var live = !!href && /^https?:/.test(location.protocol);

  function queue() { return raw(queueKey, {}) || {}; }

  function enqueue(namespace, field, value) {
    var q = queue();
    if (!q[namespace]) { q[namespace] = {}; }
    // Date.now() and not performance.now(): the queue outlives the page, and a
    // monotonic counter resets on reload. Only ever used as a DIFFERENCE
    // against this same device, never compared with another device's clock.
    q[namespace][field] = { v: value, at: Date.now() };
    put(queueKey, q);
    announce();
  }

  function pendingCount() {
    var q = queue(), n = 0;
    for (var namespace in q) {
      for (var field in q[namespace]) { n += 1; }
    }
    return n;
  }

  function announce() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](state()); } catch (e) { /* never break the page */ }
    }
  }

  var blocked = false;
  function state() {
    if (blocked) { return "blocked"; }
    return pendingCount() ? "pending" : "clean";
  }

  function absorb(fields) {
    for (var namespace in fields) {
      for (var field in fields[namespace]) {
        put(prefix + ":" + namespace + ":" + field, fields[namespace][field].v);
      }
    }
    announce();
  }

  function flush() {
    if (!live || inflight) { return; }
    var q = queue();
    var sending = {}, count = 0, now = Date.now();
    for (var namespace in q) {
      sending[namespace] = {};
      for (var field in q[namespace]) {
        var entry = q[namespace][field];
        // An AGE, not a timestamp. The server subtracts it from its own clock,
        // so a flush that arrives late still lands where it happened.
        sending[namespace][field] = { v: entry.v, age_ms: now - entry.at };
        count += 1;
      }
    }
    if (!count) { return; }
    inflight = true;
    fetch(href, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fields: sending }),
      keepalive: true,
      credentials: "same-origin"
    }).then(function (response) {
      if (response.status === 403 || response.status === 401) {
        // NOT offline. Keep the queue and say so — a silent 403 that eats marks
        // is the failure this whole design is trying not to have.
        blocked = true;
        throw new Error("sync blocked");
      }
      if (!response.ok) { throw new Error("sync " + response.status); }
      return response.json();
    }).then(function (payload) {
      blocked = false;
      // Clear ONLY what was sent: an edit made during the round trip is still
      // pending and must survive.
      var remaining = queue();
      for (var ns in sending) {
        for (var f in sending[ns]) {
          if (remaining[ns] && remaining[ns][f] &&
              remaining[ns][f].at === q[ns][f].at) {
            delete remaining[ns][f];
          }
        }
        if (remaining[ns] && !Object.keys(remaining[ns]).length) {
          delete remaining[ns];
        }
      }
      put(queueKey, remaining);
      if (payload && payload.fields) { absorb(payload.fields); }
      inflight = false;
      announce();
    }).catch(function () {
      inflight = false;
      announce();
      if (!retry) {
        retry = setTimeout(function () { retry = null; flush(); }, RETRY_MS);
      }
    });
  }

  function schedule() {
    if (timer) { clearTimeout(timer); }
    timer = setTimeout(function () { timer = null; flush(); }, FLUSH_MS);
  }

  if (live) {
    fetch(href, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (payload) {
        if (payload && payload.fields) { absorb(payload.fields); }
      })
      .catch(function () { /* offline: the cache is already rendered */ });
    window.addEventListener("online", flush);
    window.addEventListener("pagehide", flush);
  }

  return {
    get: function (namespace, field, fallback) {
      return raw(prefix + ":" + namespace + ":" + field, fallback);
    },
    set: function (namespace, field, value) {
      put(prefix + ":" + namespace + ":" + field, value);
      if (live) { enqueue(namespace, field, value); schedule(); }
    },
    pending: pendingCount,
    state: state,
    onchange: function (fn) { listeners.push(fn); }
  };
}
'''

# Kept OUT of any f-string. JS is brace-dense, and every brace would need
# doubling — which is exactly how this page's CSS used to get silently mangled
# back when the whole generator lived in a shell heredoc.
_JS = """
(function () {
  var PREFIX = "coldread-library";
  var body = document.body;

  // Sharing is opt-in, and this page works without it: the booth has no
  // network, and a library served off a memory stick has no service to talk to.
  var syncHref = body.dataset.sync || "";
  var shared = syncHref && typeof coldreadSync === "function"
    ? coldreadSync(PREFIX, syncHref)
    : null;

  // Namespace plus field, never one blob per namespace. Two devices that each
  // mark a DIFFERENT script while offline must both survive the merge, and that
  // only works when every filename is its own field with its own timestamp.
  // The key shape is the same whether sharing is on or off, so switching it on
  // does not orphan what is already stored.
  // Losing state is acceptable; refusing to render is not. Same contract as
  // reader.js, which shares this key shape.
  var store = {
    get: function (namespace, field, fallback) {
      if (shared) { return shared.get(namespace, field, fallback); }
      try {
        var raw = localStorage.getItem(PREFIX + ":" + namespace + ":" + field);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (e) { return fallback; }
    },
    set: function (namespace, field, value) {
      if (shared) { shared.set(namespace, field, value); return; }
      try {
        localStorage.setItem(
          PREFIX + ":" + namespace + ":" + field, JSON.stringify(value));
      } catch (e) { /* in-memory only */ }
    }
  };

  // A one-time move off the single-blob keys this page used before each field
  // carried its own timestamp. Written straight to localStorage rather than
  // through store.set: these marks are of unknown age, and queueing them would
  // let a stale local blob outrank a newer decision made on another device.
  ["read", "open"].forEach(function (namespace) {
    var legacy = PREFIX + ":" + namespace;
    var blob = null;
    try { blob = JSON.parse(localStorage.getItem(legacy)); }
    catch (e) { blob = null; }
    if (!blob || typeof blob !== "object" || blob.length !== undefined) {
      return;
    }
    Object.keys(blob).forEach(function (field) {
      var key = PREFIX + ":" + namespace + ":" + field;
      try {
        if (localStorage.getItem(key) === null) {
          localStorage.setItem(key, JSON.stringify(blob[field]));
        }
      } catch (e) { /* in-memory only */ }
    });
    try { localStorage.removeItem(legacy); } catch (e) { /* already gone */ }
  });

  var rows = [].slice.call(document.querySelectorAll("li[data-key]"));
  var sections = [].slice.call(document.querySelectorAll("details[data-channel]"));

  // --- read state ----------------------------------------------------------
  // No longer pruned against what is served. Pruning kept superseded drafts out
  // of storage, but per-field state cannot be pruned by omission: it would take
  // a tombstone per vanished draft, and tombstones travel to every device.
  // Leaving the fields alone also means read state survives a draft being
  // temporarily unpublished.
  function isRead(li) { return !!store.get("read", li.dataset.key, false); }

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
    // A tombstone, not a deletion. false carries a timestamp and can therefore
    // outrank a stale true from another device; an absent key could not.
    store.set("read", li.dataset.key, !isRead(li));
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
  sections.forEach(function (section) {
    var channel = section.dataset.channel;
    if (store.get("open", channel, false)) { section.open = true; }
    section.addEventListener("toggle", function () {
      store.set("open", channel, section.open);
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
  var stamp = (fresh ? "reloaded " : "loaded ") +
    new Date().toLocaleTimeString();

  // When sharing is on the line always says which state it is in. Saying
  // nothing would make "no service configured" look identical to "configured
  // and quietly dropping every mark", which is the one failure this whole
  // feature exists to avoid.
  function note() {
    if (!shared) { return ""; }
    if (shared.state() === "blocked") { return " \\u00b7 sync blocked"; }
    var n = shared.pending();
    return n ? " \\u00b7 " + n + " pending" : " \\u00b7 synced";
  }

  function showLoaded() { loaded.textContent = stamp + note(); }

  showLoaded();
  if (fresh) {
    loaded.className = "flash";
    setTimeout(function () { loaded.className = ""; }, 2500);
  }
  document.getElementById("refresh").addEventListener("click", function () {
    this.className = "spin";
    loaded.textContent = "reloading\\u2026";
    location.replace(location.pathname + "?r=" + Date.now());
  });

  if (shared) {
    // The background GET lands well after this script has rendered from the
    // cache, so whatever it wins has to be re-placed when it arrives. place()
    // and refreshCounts() are idempotent, which is what makes it safe to run
    // this on every queue change as well.
    shared.onchange(function () {
      rows.forEach(place);
      refreshCounts();
      showLoaded();
    });
  }
}());
"""


def render_index(
    entries: Sequence[IndexEntry],
    order: Sequence[str] | None = None,
    labels: dict[str, str] | None = None,
    sync: str | None = None,
) -> str:
    """Render the library page listing `entries`, grouped by channel.

    `order` and `labels` are the deployment's channel opinions, threaded in as
    arguments rather than read from a module global: the names belong to a
    `channels.json` beside the scripts, not to this file. `sync` is the href of
    a state service; without it the page is the local-only page it always was.
    """
    sections = "\n".join(
        _section(channel, group, labels or {})
        for channel, group in _grouped(entries, tuple(order or ()))
    )
    noun = "script" if len(entries) == 1 else "scripts"
    sync_attr = f' data-sync="{html.escape(sync, quote=True)}"' if sync else ""
    # Inlined rather than linked, like every other asset here: the page has to
    # render from a file:// tab and off a host with nothing installed on it.
    sync_js = "<script>" + _SYNC_JS + "</script>\n" if sync else ""
    return (
        '<!doctype html>\n<html lang="en"><head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
        '<meta name="theme-color" content="#121212">\n'
        "<title>ColdRead</title>\n<style>"
        + _CSS
        + f"</style>\n</head>\n<body{sync_attr}>\n<header>"
        + f"<h1>ColdRead &mdash; {len(entries)} {noun}</h1>"
        + '<button id="refresh" type="button" aria-label="Reload library"'
        ">&#10227;</button></header>\n"
        '<div id="loaded"></div>\n'
        + sections
        + "\n"
        + sync_js
        + "<script>"
        + _JS
        + "</script>\n</body></html>\n"
    )


def main(directory: str, sync: str | None = None) -> None:
    """Write `directory/index.html` describing the read-views in it.

    `channels.json` is read from the same directory that is being indexed, so
    the display config arrives on the same rsync as the content it describes.
    `sync` is the optional state-service href: a deployment turns sharing on by
    passing it as a second argument, without editing anything in this repo.
    """
    root = pathlib.Path(directory)
    order, labels = read_channel_config(root)
    entries = [
        entry_for(path)
        for path in sorted(root.glob("*.html"))
        if path.name != "index.html"
    ]
    (root / "index.html").write_text(
        render_index(entries, order=order, labels=labels, sync=sync),
        encoding="utf-8",
    )
    print(f"    index: {len(entries)} entries")


if __name__ == "__main__":
    if not 2 <= len(sys.argv) <= 3:
        print("usage: library.py <directory> [sync-href]", file=sys.stderr)
        raise SystemExit(64)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
