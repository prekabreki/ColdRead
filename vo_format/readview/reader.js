"use strict";
(function () {
  var WPM_MIN = 40, WPM_MAX = 400, WPM_STEP = 5, WPM_DEFAULT = 150;
  var SIZE_MIN = 12, SIZE_MAX = 48, SIZE_STEP = 2, SIZE_DEFAULT = 20;

  var body = document.body;
  var wordsPerLine = parseFloat(body.dataset.wordsPerLine) || 7;
  var storeKey = "coldread:" + (body.dataset.title || "untitled");

  var el = {
    play: document.getElementById("play"),
    slower: document.getElementById("slower"),
    faster: document.getElementById("faster"),
    smaller: document.getElementById("smaller"),
    bigger: document.getElementById("bigger"),
    theme: document.getElementById("theme"),
    status: document.getElementById("status"),
    awake: document.getElementById("awake"),
    firstLine: document.querySelector(".l:not(.hdr)")
  };

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
  var running = false;
  var held = false;
  var lastFrame = 0;
  var dragStartY = 0, dragStartPos = 0;

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function lineHeightPx() {
    // Measured, not assumed: it changes with font size and orientation.
    if (!el.firstLine) { return size * 1.55; }
    var h = el.firstLine.getBoundingClientRect().height;
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

  function paintStatus() {
    el.status.textContent = wpm + " wpm" + (running ? "" : " ▌▌");
    el.play.textContent = running ? "▌▌" : "▶";
  }

  function seek(next) {
    pos = clamp(next, 0, maxScroll());
    window.scrollTo(0, pos);
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
    paintStatus();
    requestAnimationFrame(frame);
  }

  function pause() {
    running = false;
    keepAwake(false);
    paintStatus();
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
    paintStatus();
  }

  function nudgeSize(delta) {
    size = clamp(size + delta, SIZE_MIN, SIZE_MAX);
    store.set("size", size);
    applySize();
  }

  // --- touch: down freezes, drag repositions, lift resumes ------------------
  document.addEventListener("touchstart", function (e) {
    if (e.target.closest("#hud, .zone")) { return; }
    held = true;
    dragStartY = e.touches[0].clientY;
    dragStartPos = pos;
  }, { passive: true });

  document.addEventListener("touchmove", function (e) {
    if (!held) { return; }
    e.preventDefault();               // stop native momentum fighting us
    seek(dragStartPos - (e.touches[0].clientY - dragStartY));
  }, { passive: false });

  document.addEventListener("touchend", function () {
    if (!held) { return; }
    held = false;
    lastFrame = 0;                    // do not credit the held time as elapsed
    store.set("pos", pos);
  }, { passive: true });

  // --- pointer (Pi screen, desktop) ----------------------------------------
  window.addEventListener("scroll", function () {
    if (!running && !held) { pos = window.scrollY; }
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

  el.play.addEventListener("click", toggle);
  el.slower.addEventListener("click", function () { nudgeWpm(-WPM_STEP); });
  el.faster.addEventListener("click", function () { nudgeWpm(WPM_STEP); });
  el.smaller.addEventListener("click", function () { nudgeSize(-SIZE_STEP); });
  el.bigger.addEventListener("click", function () { nudgeSize(SIZE_STEP); });
  el.theme.addEventListener("click", function () {
    theme = theme === "dark" ? "light" : "dark";
    store.set("theme", theme);
    applyTheme();
  });

  window.addEventListener("pagehide", function () { store.set("pos", pos); });

  applySize();
  applyTheme();
  seek(pos);
  paintStatus();
})();
