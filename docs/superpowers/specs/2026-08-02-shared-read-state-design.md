# Shared read state across devices — design

Read marks, completed scripts and reading preferences currently live in each
device's `localStorage`, so a phone and a desktop pointed at the same library
disagree about everything. This gives the library one shared state, kept on
whatever host serves it, without giving up the property that matters most: a
read-view still works with the network off.

## Problem

Both pages persist through an identical `store` object — `reader.js:40-51` and
the copy inside `library.py:203-214`. Same key shape, same swallow-errors
contract, same origin-scoped browser storage. Which means:

- Marking a script complete on the phone leaves the desktop showing it unread.
- A resume mark set in the booth is invisible on the machine the script was
  written on.
- Every preference — wpm, type size, theme, scroll position, which channels are
  expanded — is per-device by accident rather than by decision.

The owner's ruling, 2026-08-02: **all of it should follow him between devices**,
including type size and theme, with the consequence accepted that `A+` on a phone
also enlarges the desktop.

## Approach

`store` is the seam. Give it a shared backing and no call site in either page has
to know synchronisation exists.

State lives on the machine that already serves the library — the only always-on
host in the deployment — behind a small stdlib HTTP service. Clients treat their
`localStorage` as an offline-first cache with a persisted write queue.

### Why not fold this into #140

#140 specs the read-only library server and says so explicitly: *"Uploading,
deleting or mutating anything through the server… It is read-only by design."*
Adding writes does not extend that spec, it replaces its threat model. #140 is
also already stale — it assumes channel subdirectories (`CL/`, `BoP/`) where the
real deployment is one flat directory with `Channel — ` filename prefixes.
Building state as a separate service ships this feature without dragging a
security-sensitive rewrite onto the critical path, and #140 can absorb the
endpoint later without any client change.

### Where the boundary sits, because this is a public repo

ColdRead ships mechanisms whose defaults name nothing of the author's. The
deployment lives in the private channel repo, next to `coldread-push.sh` — which
is already the precedent for exactly this split.

| Generic — ColdRead | Deployment — CassetteLore `tools/` |
|---|---|
| `state.py`: `--state-file`, `--port`, `--bind`, `--token-file` | the systemd unit and its `MemoryMax` |
| `sync.js`, and the `--sync` flag that emits it | the reverse-proxy path mount |
| the merge semantics and the queue | the token, and pulling `state.json` back on each push |
| `channels.json` *support* | `channels.json` *content* |

The client requests a **relative** path, so the page never knows what kind of
machine is answering.

## Components

**`vo_format/readview/state.py`** — new. Stdlib-only, `ThreadingHTTPServer`,
entry point `coldread-state`. Same stdlib constraint and same reason as
`library.py`: it runs on a host with nothing installed, and it is small enough to
copy across on its own.

**`vo_format/readview/sync.js`** — new asset. Read-through cache over
`localStorage`, persisted pending queue, debounced flush.

**`channels.json`** — optional, read from the library directory. Retires the
hardcoded `CHANNEL_LABELS` and `CHANNEL_ORDER` at `library.py:32-35`:

```json
{ "order": ["CassetteLore", "Birds of Play"],
  "labels": { "CassetteLore": "Cassette Lore" } }
```

`library.py` already receives the library directory as `argv[1]`, so the config
travels with the content on the same rsync, and an absent file falls back to
today's behaviour — which already renders an unknown channel verbatim rather than
dropping it. No new mechanism, and the author's two channel names leave the public
repo.

### `sync.js` has to exist twice, and a test has to enforce that

`library.py` is piped to a remote interpreter as `python3 - <dir>`, so it has no
`__file__` and cannot read a sibling asset. That is exactly why its CSS and JS are
inline string literals today. `render.py`, by contrast, reads assets through
`importlib.resources`.

So the client ships as a canonical `sync.js` **and** as an embedded copy in
`library.py`, with a test asserting the two are byte-identical. Duplication a test
enforces is honest; duplication on trust is how this page's CSS was silently
mangled back when the generator lived in a shell heredoc.

## Data model

A two-level map. Timestamps live at the **field** level, not the namespace level:

```json
{
  "read": {
    "CassetteLore — Bloodborne Ep1 v3 - formatted - readview.html":
      { "v": true, "t": 1770000000123 }
  },
  "open": { "CassetteLore": { "v": true, "t": 1770000000456 } },
  "script:Bloodborne Ep1 v3 - formatted": {
    "wpm":   { "v": 165,  "t": 1770000001000 },
    "size":  { "v": 24,   "t": 1770000001100 },
    "theme": { "v": "dark", "t": 1770000001200 },
    "pos":   { "v": 1840, "t": 1770000009000 },
    "mark":  { "v": { "line": 42, "start": 3, "end": 10, "text": "whisper" },
               "t": 1770000008000 }
  }
}
```

**Why field-level and not namespace-level.** The obvious schema makes `read` a
single value holding `{filename: true}`. Two devices that each mark a *different*
script complete while offline would then resolve by whole-map last-write-wins and
one device's marks would vanish — inside the one key the owner cares most about.
Field-level timestamps make that case merge correctly.

**Un-marking writes a tombstone**, `{"v": false, "t": …}`, rather than deleting the
field. A deletion carries no timestamp and therefore cannot outrank a stale
`true`; the row would come back on the next sync.

**No pruning in v1.** `library.py` already prunes its *display* against the files
on disk, and the whole store is a few KB for dozens of scripts. Letting superseded
drafts' fields persist also means read state survives a draft being temporarily
unpublished. If it ever matters, the service can take an optional library
directory and prune against it.

## The HTTP contract

```
GET  /state  → 200 {"now": <ms>, "fields": { … as above … }}
POST /state  → 200 {"now": <ms>, "fields": { … merged … }}
anything else → 405
```

POST body sends values with **ages, not timestamps**:

```json
{ "fields": {
    "read": { "A.html": { "v": true, "age_ms": 10800000 } },
    "script:A": { "pos": { "v": 1840, "age_ms": 250 } } } }
```

The POST returns the full merged state, so one round trip converges the client.

### Clocks: the server stamps, and ages are what preserve ordering

A phone, a desktop and a Pi do not agree about the time, so no device's wall clock
may enter the merge. The server stamps every write with its own clock.

But stamping purely on *arrival* loses action order: a device flushing a mark it
made three hours ago in the booth would overwrite a change made on the desktop ten
minutes ago, simply because it reconnected later. So each queued field carries how
long ago it happened, and the server computes `t = now - min(age_ms, MAX_AGE)`.

This is safe precisely because **a device only ever measures a difference against
itself.** `age_ms` is `Date.now() - enqueued_at`, both read on the same device;
drift over a few hours is irrelevant and no absolute value is ever compared across
devices. The queue is persisted, so `enqueued_at` has to survive a page reload,
which is why it is `Date.now()` rather than `performance.now()`.

Guards, both cheap and both closing a real class of bug:

- `age_ms` missing, negative (the device's clock moved backwards) or non-numeric →
  treated as `0`. Clamped above at `MAX_AGE` (7 days) so a bad value cannot write
  arbitrarily far into the past.
- The server never issues a stamp less than or equal to the highest it has already
  issued, so an NTP correction on the host cannot reorder history.

**Merge rule:** apply an incoming field only if its computed `t` is greater than
the stored `t`. Equal loses, so a replayed flush is idempotent.

### Auth and method surface

Same shape as #140, for the same reasons — the host is reachable from anywhere its
private network reaches, so "it's only on my LAN" is not a defence:

- A 256-bit urlsafe token, generated on first run, persisted to `--token-file`.
  `?k=<token>` sets an `HttpOnly`, `SameSite=Strict` cookie. Compared with
  `hmac.compare_digest`.
- `Origin` / `Sec-Fetch-Site` rejected when **present and cross-origin**; absence
  allowed, because a bookmark tap sends neither. A `Host` check alone is not
  enough here: the browser derives `Host` from the request target, so it stops DNS
  rebinding but not a blind cross-origin POST.
- `GET` and `POST` only. Everything else is a flat 405.
- Binds `127.0.0.1` by default; a reverse proxy is what exposes it.
- `log_message` overridden to strip the query string, or the token lands in the
  journal on every request.

Mounting the service at a **path on the same origin** as the library is what keeps
this cheap: `fetch("/state")` is then same-origin, so there is no CORS preflight,
the cookie is sent, and the origin check is an equality test rather than an
allowlist.

## Data flow

**Load.** Render from `localStorage` immediately — never block on the network,
because the booth is the case that matters. Then `fetch("/state")` in the
background and merge by timestamp, writing anything the server won back to
`localStorage`.

One exception, and it is the only place sync is allowed to lose: **`pos` and
`mark` are applied only if the response lands before the reader has touched the
page.** A server scroll position yanking the script out from under someone
mid-read is worse than not syncing it. After first interaction they are stored but
not applied, and take effect on the next load.

**Write.** `store.set` writes `localStorage` as it does today, and appends the
field to a pending queue that is itself persisted — otherwise closing the booth
page loses every offline mark, which is the whole feature. Flush triggers:

- debounced ~1s after a change,
- on `pagehide`, with `keepalive: true` — a plain `fetch` is cancelled by the
  navigation, so the last flush of a session is exactly the one that would go
  missing,
- on the `online` event,
- on a backoff retry while anything is pending.

Only the fields actually sent are cleared from the queue on success, so an edit
made during the round trip is not dropped.

**Sync is opt-in, `--sync <href>`.** Default off, mirroring `--library`'s
reasoning: a lone read-view has no library to return to, and equally has no state
service to talk to. No page should issue a request its deployment did not ask for.
The client is also inert whenever `location.protocol` is not `http(s)` — a
read-view opened as a file keeps working, silently local.

## Error handling

The repo's convention is loud failure, and this feature has a failure mode that is
worse than an error: appearing to work.

- **A 403 is not offline.** A bad token or a rejected origin must surface as
  `sync blocked` and **keep** the queue. Silently eating marks is the exact shape
  that has bitten this project twice before.
- **A corrupt or absent state file serves `{}` and says so** on stderr. It never
  lets a page conclude it is synced.
- Writes are atomic: temp file plus `os.replace`, with one rotating backup copy
  kept. The state file is the only thing on the serving host not reconstructible
  from a repo, so the push should pull it back opportunistically.
- One `threading.Lock` around read-modify-write. `ThreadingHTTPServer` means two
  devices really can POST at once, and the failure would be a lost field rather
  than an exception.
- Refuses to start, named and non-zero, on an unwritable state path, an unreadable
  token file, or a busy port.

**Visible state.** `⇅` in the HUD, amber while anything is pending, flashing green
briefly on a successful flush; the library page extends its existing
`loaded HH:MM:SS` line the same way. Silence means clean — but success still shows
something changing, because a control that never visibly does anything reads as
dead, which is the lesson the `⟳` button already taught this page.

## Testing

Server-side, all straightforward and all worth having:

- Merge semantics: field-level last-write-wins, tombstones outranking stale
  `true`, equal timestamps losing, `age_ms` shifting a write into the past, and
  the clamp and negative-age guards.
- The monotonic-stamp guard: two writes in the same millisecond, and a simulated
  backwards clock, both still produce strictly increasing stamps.
- 405 on `PUT`/`DELETE`/`HEAD`-with-body; 403 on a wrong token and on a
  cross-origin `Origin`; 200 when `Origin` is absent.
- The token never appears in a log line — grep a real captured log, not the
  override's source.
- `state.py` imports only the stdlib, by AST walk, mirroring
  `test_readview_library.py::test_module_imports_only_the_stdlib`.
- `sync.js` and the copy embedded in `library.py` are byte-identical.
- `channels.json`: honoured when present, ignored when malformed (falling back
  rather than raising), and absent behaves exactly as today.

**What cannot be tested here, stated rather than implied.** The queue and flush
logic is JavaScript and this repo has no JS test runner; adding a Node toolchain
to a Python package to cover ~80 lines is the wrong trade. So the contract is
tested from the server side, a render test asserts `sync.js` is inlined when
`--sync` is passed and absent when it is not, and **the two-device
offline→online round trip is a manual check that belongs in the PR**: mark on one
device with the network off, reconnect, confirm the other device converges and
that nothing was lost in either direction.

Every new test must be Windows-clean. `tests/test_readview_library.py:313` pipes
source through a `text=True` subprocess and fails on Windows under cp1252; do not
reproduce that pattern.

## Out of scope

- Rewriting or superseding #140. This service is additive and #140 can absorb the
  endpoint later.
- Multi-user support. One person, several devices. There is no per-user
  partitioning and no attempt at one.
- Real-time push. Polling on load and flushing on change is enough for a single
  reader; no long-poll, no WebSocket.
- Syncing the scripts themselves. The library is still published by rsync.
- Pruning superseded drafts out of the store.
