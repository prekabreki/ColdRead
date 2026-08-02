"use strict";
(function () {
  var WPM_MIN = 40, WPM_MAX = 400, WPM_STEP = 10, WPM_DEFAULT = 150;
  var SIZE_MIN = 12, SIZE_MAX = 48, SIZE_STEP = 2, SIZE_DEFAULT = 20;

  // Momentum. FLING_DECAY is per 16ms of glide, so the feel is frame-rate
  // independent; SAMPLE_MS is the window whose travel counts as the throw.
  var FLING_MIN = 40, FLING_STOP = 20, FLING_DECAY = 0.94, SAMPLE_MS = 80;

  // Resume mark. HOLD_SLOP has to stay well under the travel of a real drag, or
  // repositioning the script would drop a marker every time.
  var HOLD_MS = 500, HOLD_SLOP = 8;

  var body = document.body;
  var wordsPerLine = parseFloat(body.dataset.wordsPerLine) || 7;
  var storeKey = "coldread:" + (body.dataset.title || "untitled");

  var el = {
    hud: document.getElementById("hud"),
    play: document.getElementById("play"),
    wpmdown: document.getElementById("wpmdown"),
    wpmup: document.getElementById("wpmup"),
    smaller: document.getElementById("smaller"),
    bigger: document.getElementById("bigger"),
    theme: document.getElementById("theme"),
    status: document.getElementById("status"),
    back: document.getElementById("back"),      // absent unless --library
    awake: document.getElementById("awake"),
    firstLine: document.querySelector(".bl")
  };

  // Every rendered line, header paragraphs included. The resume mark stores an
  // index into this list, so it only has to be self-consistent within a page.
  var lines = [].slice.call(document.querySelectorAll(".l"));

  // Safari blocks storage for file:// origins, and Phase 1 is delivered as a
  // file. Losing preferences is acceptable; refusing to render is not.
  var store = {
    get: function (key, fallback) {
      try {
        var raw = localStorage.getItem(storeKey + ":" + key);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (e) { return fallback; }
    },
    set: function (key, value) {
      try { localStorage.setItem(storeKey + ":" + key, JSON.stringify(value)); }
      catch (e) { /* in-memory only */ }
    }
  };

  var wpm = store.get("wpm", WPM_DEFAULT);
  var size = store.get("size", SIZE_DEFAULT);
  var theme = store.get("theme", "dark");
  var pos = store.get("pos", 0);
  var mark = store.get("mark", null);
  var running = false;
  var held = false;
  var lastFrame = 0;
  var dragStartY = 0, dragStartPos = 0;
  var samples = [], gliding = false, glideV = 0, glideLast = 0;
  var holdTimer = null, holdX = 0, holdY = 0, touchSeen = false;
  var markEl = null;

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function lineHeightPx() {
    // Measured, not assumed: it changes with font size and orientation.
    // getComputedStyle, not a bounding-rect measurement: the rect of a block
    // <p> is the whole box, so a probed line that WRAPS would report double
    // the line height and the page would scroll at twice the displayed wpm.
    if (!el.firstLine) { return size * 1.55; }
    var h = parseFloat(getComputedStyle(el.firstLine).lineHeight);
    return h > 0 ? h : size * 1.55;
  }

  function pxPerSecond() {
    var linesPerSecond = (wpm / 60) / wordsPerLine;
    return linesPerSecond * lineHeightPx();
  }

  function maxScroll() {
    return Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
  }

  function applySize() {
    document.documentElement.style.setProperty("--font-size", size + "px");
  }

  function applyTheme() {
    document.documentElement.setAttribute("data-theme", theme);
    el.theme.textContent = theme === "dark" ? "☾" : "☀";
  }

  function percent() {
    var max = maxScroll();
    // A script shorter than the viewport is entirely on screen, so it is done,
    // not undefined. This is also the divide-by-zero guard.
    if (max <= 0) { return 100; }
    return clamp(Math.round((pos / max) * 100), 0, 100);
  }

  function paintHud() {
    var done = percent();
    el.status.textContent = done + "% · " + wpm + " wpm" + (running ? "" : " ▌▌");
    el.play.textContent = running ? "▌▌" : "▶";
    // Drives #hud::after, and set on the HUD rather than on the root: this runs
    // every frame of a scroll, and a custom property on documentElement
    // invalidates style for everything that might inherit it — the whole script.
    el.hud.style.setProperty("--progress", done + "%");
  }

  function seek(next) {
    pos = clamp(next, 0, maxScroll());
    window.scrollTo(0, pos);
    paintHud();
  }

  function frame(now) {
    if (!running) { return; }
    if (lastFrame) {
      var dt = (now - lastFrame) / 1000;
      // A backgrounded tab returns a huge delta; skipping ahead a page would be
      // worse than losing the interval.
      if (dt > 0 && dt < 0.5 && !held) { seek(pos + pxPerSecond() * dt); }
    }
    lastFrame = now;
    if (pos >= maxScroll()) { pause(); return; }
    requestAnimationFrame(frame);
  }

  function play() {
    if (running) { return; }
    running = true;
    lastFrame = 0;
    keepAwake(true);
    paintHud();
    requestAnimationFrame(frame);
  }

  function pause() {
    running = false;
    keepAwake(false);
    paintHud();
    store.set("pos", pos);
  }

  function toggle() { running ? pause() : play(); }

  function keepAwake(on) {
    if (on) {
      var playing = el.awake.play();
      if (playing && playing.catch) { playing.catch(function () {}); }
      if (navigator.wakeLock && navigator.wakeLock.request) {
        // Only available over a real secure context; harmless when it is not.
        navigator.wakeLock.request("screen").catch(function () {});
      }
    } else {
      el.awake.pause();
    }
  }

  function nudgeWpm(delta) {
    wpm = clamp(wpm + delta, WPM_MIN, WPM_MAX);
    store.set("wpm", wpm);
    paintHud();
  }

  function nudgeSize(delta) {
    size = clamp(size + delta, SIZE_MIN, SIZE_MAX);
    store.set("size", size);
    applySize();
    // Resizing changes scrollHeight, so the same pos is now a different
    // fraction of the script. Repaint or the percentage silently goes stale.
    paintHud();
  }

  // --- resume mark: hold a word to say "this is where I stopped" ------------
  // No per-word spans: a 3000-word script would be 3000 elements to serve, to
  // restyle on every A+, and to keep offsets into. The caret APIs give us the
  // word under the finger without touching the DOM at all.
  function caretAt(x, y) {
    if (document.caretRangeFromPoint) {          // WebKit, Blink
      var range = document.caretRangeFromPoint(x, y);
      return range ? { node: range.startContainer, offset: range.startOffset } : null;
    }
    if (document.caretPositionFromPoint) {       // Gecko, and the standard
      var caret = document.caretPositionFromPoint(x, y);
      return caret ? { node: caret.offsetNode, offset: caret.offset } : null;
    }
    return null;
  }

  function wordAt(text, offset) {
    var i = Math.min(offset, text.length - 1);
    if (i < 0) { return null; }
    // A caret in the gap between words belongs to the word before it — that is
    // where the finger was aiming, and it makes the trailing edge forgiving.
    if (/\s/.test(text.charAt(i)) && i > 0) { i -= 1; }
    if (/\s/.test(text.charAt(i))) { return null; }
    var start = i, end = i + 1;
    while (start > 0 && !/\s/.test(text.charAt(start - 1))) { start -= 1; }
    while (end < text.length && !/\s/.test(text.charAt(end))) { end += 1; }
    return [start, end];
  }

  function clearMark() {
    if (!markEl) { return; }
    var parent = markEl.parentNode;
    while (markEl.firstChild) { parent.insertBefore(markEl.firstChild, markEl); }
    parent.removeChild(markEl);
    parent.normalize();
    markEl = null;
  }

  // The ONLY writer of the mark, so the DOM and storage cannot disagree. An
  // earlier shape cleared the highlight and then returned early on some paths,
  // which left storage holding a mark that was no longer on the page — it came
  // back on the next load.
  function commitMark(m) {
    clearMark();
    mark = m && paintMark(m) ? m : null;
    store.set("mark", mark);
  }

  // Offset of `node` within its line's whole text. A line that currently holds
  // the <mark> has three text nodes, so a caret offset into one of them is not
  // a line offset; this is what lets a press resolve identically either way,
  // without having to tear the highlight down first to measure.
  function lineOffset(line, node) {
    var walk = document.createTreeWalker(line, NodeFilter.SHOW_TEXT, null, false);
    var total = 0, n;
    while ((n = walk.nextNode())) {
      if (n === node) { return total; }
      total += n.data.length;
    }
    return -1;
  }

  function paintMark(m) {
    if (!m) { return false; }
    var line = lines[m.line];
    if (!line) { return false; }
    var node = line.firstChild;
    if (!node || node.nodeType !== 3) { return false; }
    // The store key carries the draft version, so a mark cannot normally reach
    // text it was not made against. This is the belt-and-braces check, and it
    // earns its place: a highlight on the wrong word is silent and misleading.
    if (m.end > node.data.length || node.data.slice(m.start, m.end) !== m.text) {
      return false;
    }
    var range = document.createRange();
    range.setStart(node, m.start);
    range.setEnd(node, m.end);
    var tag = document.createElement("mark");
    tag.className = "resume";
    try { range.surroundContents(tag); } catch (e) { return false; }
    markEl = tag;
    return true;
  }

  function setMark(x, y) {
    var hit = caretAt(x, y);
    if (!hit || hit.node.nodeType !== 3) { return; }
    var line = hit.node.parentNode;
    if (line && line.tagName === "MARK") { line = line.parentNode; }
    var index = line ? lines.indexOf(line) : -1;
    // Every failure here LEAVES THE MARK ALONE rather than clearing it. A press
    // that lands in the gutter beside the text must not destroy the resume point
    // it was aiming for.
    if (index < 0) { return; }
    var base = lineOffset(line, hit.node);
    if (base < 0) { return; }
    var text = line.textContent;
    var span = wordAt(text, base + hit.offset);
    if (!span) { return; }

    // Pressing the word that already carries the mark clears it. Compared by
    // offset rather than by what the caret landed inside, so pressing any part
    // of the marked word does it.
    var same = mark && mark.line === index &&
               mark.start === span[0] && mark.end === span[1];
    commitMark(same ? null : {
      line: index,
      start: span[0],
      end: span[1],
      text: text.slice(span[0], span[1])
    });
  }

  function startHold(x, y) {
    cancelHold();
    holdX = x;
    holdY = y;
    holdTimer = setTimeout(function () {
      holdTimer = null;
      setMark(holdX, holdY);
    }, HOLD_MS);
  }

  function cancelHold() {
    if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
  }

  function movedTooFar(x, y) {
    return Math.abs(x - holdX) > HOLD_SLOP || Math.abs(y - holdY) > HOLD_SLOP;
  }

  // --- momentum -------------------------------------------------------------
  function sample(now) {
    samples.push({ t: now, p: pos });
    // Keep exactly one sample older than the window, so the window always has a
    // baseline to measure travel from.
    while (samples.length > 2 && now - samples[1].t > SAMPLE_MS) { samples.shift(); }
  }

  function velocity(now) {
    if (samples.length < 2) { return 0; }
    var last = samples[samples.length - 1];
    // A finger that came to rest before lifting is not a fling, however fast it
    // was travelling a moment before. No touchmove fires while it is still, so
    // without this check the stale samples would throw the page.
    if (now - last.t > SAMPLE_MS) { return 0; }
    var first = samples[0];
    var dt = (last.t - first.t) / 1000;
    return dt > 0 ? (last.p - first.p) / dt : 0;
  }

  function glideFrame(now) {
    if (!gliding) { return; }
    var dt = now - glideLast;
    glideLast = now;
    if (dt > 0 && dt < 500) {
      seek(pos + glideV * (dt / 1000));
      glideV *= Math.pow(FLING_DECAY, dt / 16);
    }
    if (Math.abs(glideV) < FLING_STOP || pos <= 0 || pos >= maxScroll()) {
      endGlide();
      return;
    }
    requestAnimationFrame(glideFrame);
  }

  function startGlide(v) {
    // `held` stays true for the whole glide, so a playing script does not fight
    // the throw; autoscroll picks up wherever the glide lands.
    gliding = true;
    glideV = v;
    glideLast = performance.now();
    requestAnimationFrame(glideFrame);
  }

  function stopGlide() { gliding = false; glideV = 0; }

  function endGlide() {
    stopGlide();
    held = false;
    lastFrame = 0;                    // do not credit the glide as elapsed time
    store.set("pos", pos);
  }

  // --- touch: down freezes, drag repositions, lift glides -------------------
  document.addEventListener("touchstart", function (e) {
    if (e.target.closest("#hud")) { return; }
    touchSeen = true;
    stopGlide();                      // a finger down always means "stop here"
    held = true;
    dragStartY = e.touches[0].clientY;
    dragStartPos = pos;
    samples = [{ t: performance.now(), p: pos }];
    startHold(e.touches[0].clientX, e.touches[0].clientY);
  }, { passive: true });

  document.addEventListener("touchmove", function (e) {
    if (!held) { return; }
    e.preventDefault();               // stop native momentum fighting us
    var touch = e.touches[0];
    if (movedTooFar(touch.clientX, touch.clientY)) { cancelHold(); }
    seek(dragStartPos - (touch.clientY - dragStartY));
    sample(performance.now());
  }, { passive: false });

  document.addEventListener("touchend", function () {
    if (!held) { return; }
    cancelHold();
    var v = velocity(performance.now());
    if (Math.abs(v) >= FLING_MIN) { startGlide(v); return; }
    held = false;
    lastFrame = 0;                    // do not credit the held time as elapsed
    store.set("pos", pos);
  }, { passive: true });

  // Without this an interrupted gesture leaves `held` set, and autoscroll stays
  // frozen for the rest of the session with the play button still showing ▌▌.
  document.addEventListener("touchcancel", function () {
    cancelHold();
    stopGlide();
    held = false;
    lastFrame = 0;
  }, { passive: true });

  // --- pointer (Pi screen, desktop) ----------------------------------------
  // The mouse path exists so the hold gesture can be checked in a browser. It
  // stands down the moment a real touch arrives, because iOS follows a tap with
  // synthetic mouse events and a second hold would toggle the mark back off.
  document.addEventListener("mousedown", function (e) {
    if (touchSeen || e.target.closest("#hud")) { return; }
    startHold(e.clientX, e.clientY);
  });

  document.addEventListener("mousemove", function (e) {
    if (holdTimer && movedTooFar(e.clientX, e.clientY)) { cancelHold(); }
  });

  document.addEventListener("mouseup", cancelHold);

  window.addEventListener("scroll", function () {
    if (!running && !held) { pos = window.scrollY; paintHud(); }
  }, { passive: true });

  // --- keyboard: also the foot-pedal path, and the Pi may have no touch -----
  window.addEventListener("keydown", function (e) {
    switch (e.key) {
      case " ": case "Enter": e.preventDefault(); toggle(); break;
      case "ArrowUp": e.preventDefault(); nudgeWpm(WPM_STEP); break;
      case "ArrowDown": e.preventDefault(); nudgeWpm(-WPM_STEP); break;
      case "PageDown": e.preventDefault(); seek(pos + window.innerHeight * 0.8); break;
      case "PageUp": e.preventDefault(); seek(pos - window.innerHeight * 0.8); break;
      case "Home": e.preventDefault(); seek(0); break;
      case "End": e.preventDefault(); seek(maxScroll()); break;
      default: break;
    }
  });

  // Tap = one step; hold = accelerating repeat, so the 40-400 range is reachable
  // in about a second without giving up precise single taps.
  function holdRepeat(node, fn) {
    var timer = null, fires = 0;

    function tick() {
      fn();
      fires += 1;
      timer = setTimeout(tick, fires < 3 ? 300 : (fires < 8 ? 150 : 80));
    }

    function start(e) {
      e.preventDefault();          // also suppresses the synthetic click
      stop();
      fn();                        // respond to the press immediately
      fires = 0;
      timer = setTimeout(tick, 400);
    }

    function stop() {
      if (timer) { clearTimeout(timer); timer = null; }
    }

    node.addEventListener("touchstart", start, { passive: false });
    node.addEventListener("touchend", stop, { passive: true });
    node.addEventListener("touchcancel", stop, { passive: true });
    node.addEventListener("mousedown", start);
    node.addEventListener("mouseup", stop);
    node.addEventListener("mouseleave", stop);
  }

  el.play.addEventListener("click", toggle);
  holdRepeat(el.wpmdown, function () { nudgeWpm(-WPM_STEP); });
  holdRepeat(el.wpmup, function () { nudgeWpm(WPM_STEP); });
  el.smaller.addEventListener("click", function () { nudgeSize(-SIZE_STEP); });
  el.bigger.addEventListener("click", function () { nudgeSize(SIZE_STEP); });
  el.theme.addEventListener("click", function () {
    theme = theme === "dark" ? "light" : "dark";
    store.set("theme", theme);
    applyTheme();
  });

  // Only rendered with --library, so every read-view without one keeps working.
  if (el.back) {
    el.back.addEventListener("click", function () {
      store.set("pos", pos);       // pagehide covers this, but this is the
                                   // one path that leaves the page on purpose
      // Cache-bust: in standalone mode Safari will happily hand back an index
      // it cached before the last push, which is how a renamed script turns
      // into a 404 on a page that looks fine.
      location.href = (body.dataset.library || "index.html") + "?r=" + Date.now();
    });
  }

  window.addEventListener("pagehide", function () { store.set("pos", pos); });

  applySize();
  applyTheme();

  // The mark beats the saved position: it is a deliberate declaration of where
  // to resume, where `pos` is only wherever the page happened to be left. The
  // cost is that reading past the mark and coming back rewinds to it.
  if (mark && paintMark(mark)) {
    // ~40% down the viewport rather than at the very top — the lines above the
    // mark are the run-up you need to hear before speaking.
    var markTop = markEl.getBoundingClientRect().top + window.scrollY;
    seek(markTop - window.innerHeight * 0.4);
  } else {
    if (mark) { commitMark(null); }   // stale: drop it from storage too
    seek(pos);
  }
})();
