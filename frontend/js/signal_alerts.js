/**
 * Optional audio feedback when a new combined final signal marker appears (BUY / SELL / EXIT).
 * Uses Web Audio API — no audio files. Dedupes by signal_id; first snapshot hydrates without sound.
 */

(function (global) {
  "use strict";

  var STORAGE_KEY = "ragx_signal_sound_enabled";

  var audioCtx = null;
  var enabled = true;
  var lastSeenSignalId = "";
  var hasHydrated = false;

  function loadEnabled() {
    try {
      return global.localStorage.getItem(STORAGE_KEY) !== "0";
    } catch {
      return true;
    }
  }

  function saveEnabled(on) {
    try {
      global.localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
    } catch {
      /* ignore */
    }
  }

  function getContext() {
    if (audioCtx) return audioCtx;
    var AC = global.AudioContext || global.webkitAudioContext;
    if (!AC) return null;
    audioCtx = new AC();
    return audioCtx;
  }

  function resumeAndRun(playFn) {
    var c = getContext();
    if (!c) return;
    var run = function () {
      try {
        playFn(c, c.currentTime);
      } catch {
        /* ignore */
      }
    };
    if (c.state === "suspended") {
      var p = c.resume();
      if (p && typeof p.then === "function") p.then(run).catch(run);
      else global.setTimeout(run, 0);
    } else {
      global.setTimeout(run, 0);
    }
  }

  /** Soft positive bell: two detuned sines, quick pitch rise then decay */
  function playBuy(c, t0) {
    var master = c.createGain();
    master.connect(c.destination);
    master.gain.setValueAtTime(0.0001, t0);
    master.gain.exponentialRampToValueAtTime(0.09, t0 + 0.02);
    master.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.32);

    var o1 = c.createOscillator();
    o1.type = "sine";
    o1.frequency.setValueAtTime(660, t0);
    o1.frequency.exponentialRampToValueAtTime(990, t0 + 0.06);
    o1.connect(master);
    o1.start(t0);
    o1.stop(t0 + 0.34);

    var o2 = c.createOscillator();
    o2.type = "sine";
    o2.frequency.setValueAtTime(990, t0);
    o2.frequency.exponentialRampToValueAtTime(1320, t0 + 0.05);
    o2.connect(master);
    o2.start(t0);
    o2.stop(t0 + 0.34);
  }

  /** Sharper, shorter alert */
  function playSell(c, t0) {
    var g = c.createGain();
    g.connect(c.destination);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(0.085, t0 + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.14);

    var o = c.createOscillator();
    o.type = "triangle";
    o.frequency.setValueAtTime(1320, t0);
    o.frequency.exponentialRampToValueAtTime(1760, t0 + 0.04);
    o.connect(g);
    o.start(t0);
    o.stop(t0 + 0.15);
  }

  /** Neutral two-step ping */
  function playExit(c, t0) {
    function ping(start, freq) {
      var g = c.createGain();
      g.connect(c.destination);
      g.gain.setValueAtTime(0.0001, start);
      g.gain.exponentialRampToValueAtTime(0.065, start + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, start + 0.16);
      var o = c.createOscillator();
      o.type = "sine";
      o.frequency.setValueAtTime(freq, start);
      o.connect(g);
      o.start(start);
      o.stop(start + 0.17);
    }
    ping(t0, 520);
    ping(t0 + 0.11, 390);
  }

  function pickLatestCombinedMarker(payload) {
    if (!payload || typeof payload !== "object") return null;
    var markers = payload.signal_markers;
    if (!markers || !markers.length) return null;
    var best = null;
    var bestT = -Infinity;
    var i;
    for (i = 0; i < markers.length; i++) {
      var m = markers[i];
      if (String(m.strategy_source || "") !== "combined_signal") continue;
      var t = Number(m.timestamp);
      if (!Number.isFinite(t)) t = 0;
      if (t >= bestT) {
        bestT = t;
        best = m;
      }
    }
    return best;
  }

  function processStrategySnapshot(payload) {
    var marker = pickLatestCombinedMarker(payload);
    var id = marker ? String(marker.signal_id || marker.id || "").trim() : "";
    var action = marker ? String(marker.action || "").toLowerCase().trim() : "";

    if (!hasHydrated) {
      hasHydrated = true;
      lastSeenSignalId = id;
      return;
    }

    if (!id || id === lastSeenSignalId) return;

    lastSeenSignalId = id;

    if (!enabled) return;
    if (action !== "buy" && action !== "sell" && action !== "exit") return;

    if (action === "buy") resumeAndRun(playBuy);
    else if (action === "sell") resumeAndRun(playSell);
    else resumeAndRun(playExit);
  }

  function bindToggle() {
    var cb = global.document.getElementById("signal-sound-toggle");
    if (!cb) return;
    cb.checked = enabled;
    cb.addEventListener("change", function () {
      enabled = !!cb.checked;
      saveEnabled(enabled);
      getContext();
      if (audioCtx && audioCtx.state === "suspended") {
        audioCtx.resume().catch(function () {});
      }
    });
  }

  function init() {
    enabled = loadEnabled();
    bindToggle();
  }

  global.RagxSignalAlerts = {
    init: init,
    processStrategySnapshot: processStrategySnapshot,
  };
})(typeof window !== "undefined" ? window : globalThis);
