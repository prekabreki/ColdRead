"use strict";
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
