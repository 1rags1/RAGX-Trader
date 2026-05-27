/**
 * Draggable panel splits for the trading terminal: left width, right width.
 * Uses CSS variables on <main>; chart container ResizeObserver (chart.js) reflows the chart.
 *
 * Persistence (localStorage, same origin):
 *   ragx_layout_left_px   — context column width (px)
 *   ragx_layout_right_px  — decision column width (px)
 * Values are validated on read; layout is re-saved after load if corrected.
 */

(function (global) {
  "use strict";

  var STORAGE_LEFT = "ragx_layout_left_px";
  var STORAGE_RIGHT = "ragx_layout_right_px";

  var MIN_LEFT = 185;
  var MAX_LEFT = 480;
  var MIN_RIGHT = 185;
  var MAX_RIGHT = 520;
  var MIN_CENTER = 260;
  var GRIP_COL_PX = 5;
  var NARROW_MAX = 860;

  var rootEl = null;
  var mqNarrow = null;
  var drag = null;
  var winResizeTimer = null;

  function readStoredPx(key, absMin, absMax, fallback) {
    try {
      var raw = global.localStorage.getItem(key);
      if (raw == null) return fallback;
      var s = String(raw).trim();
      if (s === "") return fallback;
      var n = Number(parseFloat(s));
      if (!Number.isFinite(n)) return fallback;
      n = Math.round(n);
      if (n < absMin || n > absMax) return Math.max(absMin, Math.min(absMax, n));
      return n;
    } catch {
      return fallback;
    }
  }

  function writeStored(key, n) {
    try {
      var v = Math.round(Number(n));
      if (!Number.isFinite(v)) return;
      global.localStorage.setItem(key, String(v));
    } catch {
      /* ignore */
    }
  }

  function isNarrow() {
    if (mqNarrow && typeof mqNarrow.matches === "boolean") return mqNarrow.matches;
    return global.innerWidth <= NARROW_MAX;
  }

  function workspaceWidth() {
    if (!rootEl) return global.innerWidth;
    var w = rootEl.clientWidth;
    return w > 80 ? w : global.innerWidth;
  }

  function parsePx(val, fallback) {
    if (val == null || val === "") return fallback;
    var n = parseFloat(String(val).trim());
    return Number.isFinite(n) ? n : fallback;
  }

  function readPairFromComputed() {
    if (!rootEl) return { L: 260, R: 320 };
    var cs = global.getComputedStyle(rootEl);
    return {
      L: parsePx(cs.getPropertyValue("--ragx-left-w"), 260),
      R: parsePx(cs.getPropertyValue("--ragx-right-w"), 320),
    };
  }

  function clampLeft(L, R) {
    var avail = workspaceWidth();
    if (avail < MIN_CENTER + MIN_LEFT + MIN_RIGHT + GRIP_COL_PX * 2) return MIN_LEFT;
    var cap = avail - GRIP_COL_PX * 2 - MIN_CENTER - R;
    cap = Math.min(cap, MAX_LEFT);
    return Math.max(MIN_LEFT, Math.min(L, cap));
  }

  function clampRight(L, R) {
    var avail = workspaceWidth();
    if (avail < MIN_CENTER + MIN_LEFT + MIN_RIGHT + GRIP_COL_PX * 2) return MIN_RIGHT;
    var cap = avail - GRIP_COL_PX * 2 - MIN_CENTER - L;
    cap = Math.min(cap, MAX_RIGHT);
    return Math.max(MIN_RIGHT, Math.min(R, cap));
  }

  function normalizeLR(L, R) {
    L = clampLeft(L, R);
    R = clampRight(L, R);
    L = clampLeft(L, R);
    return { L: L, R: R };
  }

  function applyToDom(L, R) {
    if (!rootEl || isNarrow()) return;
    var n = normalizeLR(L, R);
    rootEl.style.setProperty("--ragx-left-w", n.L + "px");
    rootEl.style.setProperty("--ragx-right-w", n.R + "px");
  }

  function clearInlineLayoutVars() {
    if (!rootEl) return;
    rootEl.style.removeProperty("--ragx-left-w");
    rootEl.style.removeProperty("--ragx-right-w");
  }

  function defaultLayoutFromViewport() {
    var w = global.innerWidth;
    var defL = Math.round(Math.min(w * 0.22, 320));
    var defR = Math.round(Math.min(w * 0.28, 380));
    defL = Math.max(MIN_LEFT, Math.min(defL, MAX_LEFT));
    defR = Math.max(MIN_RIGHT, Math.min(defR, MAX_RIGHT));
    return { L: defL, R: defR };
  }

  function loadLayoutForApply() {
    var d = defaultLayoutFromViewport();
    return {
      L: readStoredPx(STORAGE_LEFT, MIN_LEFT, MAX_LEFT, d.L),
      R: readStoredPx(STORAGE_RIGHT, MIN_RIGHT, MAX_RIGHT, d.R),
    };
  }

  function persist(L, R) {
    writeStored(STORAGE_LEFT, L);
    writeStored(STORAGE_RIGHT, R);
  }

  function clearHandleActiveClass(el) {
    if (el && el.classList) el.classList.remove("ragx-handle--active");
  }

  function cancelActiveDrag() {
    if (!drag) return;
    var el = drag.handle;
    try {
      if (drag.pointerId != null) el.releasePointerCapture(drag.pointerId);
    } catch {
      /* ignore */
    }
    try {
      el.removeEventListener("pointermove", onPointerMove);
      el.removeEventListener("pointerup", endDrag);
      el.removeEventListener("pointercancel", endDrag);
    } catch {
      /* ignore */
    }
    clearHandleActiveClass(el);
    setBodyResizing(false);
    drag = null;
  }

  function resetLayout() {
    cancelActiveDrag();
    var d = defaultLayoutFromViewport();
    var n = normalizeLR(d.L, d.R);
    if (rootEl && !isNarrow()) {
      applyToDom(n.L, n.R);
    }
    persist(n.L, n.R);
  }

  function setBodyResizing(on) {
    var b = global.document.body;
    if (!b) return;
    b.classList.toggle("ragx-resizing", on);
    b.classList.toggle("ragx-resizing--col", on);
    b.classList.remove("ragx-resizing--row");
  }

  function onPointerMove(ev) {
    if (!drag || !rootEl) return;
    if (drag.kind === "left") {
      var d = ev.clientX - drag.startX;
      var L = clampLeft(drag.startL + d, drag.startR);
      applyToDom(L, drag.startR);
    } else if (drag.kind === "right") {
      var d2 = ev.clientX - drag.startX;
      var R = clampRight(drag.startL, drag.startR - d2);
      applyToDom(drag.startL, R);
    }
  }

  function endDrag(ev) {
    if (!drag) return;
    var el = drag.handle;
    try {
      if (ev && ev.pointerId != null) el.releasePointerCapture(ev.pointerId);
    } catch {
      /* ignore */
    }
    el.removeEventListener("pointermove", onPointerMove);
    el.removeEventListener("pointerup", endDrag);
    el.removeEventListener("pointercancel", endDrag);
    clearHandleActiveClass(el);
    setBodyResizing(false);
    drag = null;
    if (!rootEl || isNarrow()) return;
    var t = readPairFromComputed();
    var n = normalizeLR(t.L, t.R);
    applyToDom(n.L, n.R);
    persist(n.L, n.R);
  }

  function startDrag(kind, ev) {
    if (isNarrow()) return;
    if (ev.button !== 0) return;
    ev.preventDefault();
    var t = readPairFromComputed();
    var handle = ev.currentTarget;
    if (kind === "left") {
      drag = {
        kind: "left",
        pointerId: ev.pointerId,
        startX: ev.clientX,
        startL: t.L,
        startR: t.R,
        handle: handle,
      };
    } else {
      drag = {
        kind: "right",
        pointerId: ev.pointerId,
        startX: ev.clientX,
        startL: t.L,
        startR: t.R,
        handle: handle,
      };
    }
    setBodyResizing(true);
    handle.classList.add("ragx-handle--active");
    try {
      handle.setPointerCapture(ev.pointerId);
    } catch {
      /* ignore */
    }
    handle.addEventListener("pointermove", onPointerMove);
    handle.addEventListener("pointerup", endDrag);
    handle.addEventListener("pointercancel", endDrag);
  }

  function nudgeHorizontal(kind, delta) {
    if (!rootEl || isNarrow()) return;
    var t = readPairFromComputed();
    var L = t.L;
    var R = t.R;
    if (kind === "left") L = clampLeft(L + delta, R);
    else R = clampRight(L, R + delta);
    var n = normalizeLR(L, R);
    applyToDom(n.L, n.R);
    persist(n.L, n.R);
  }

  function onKeyDown(kind, ev) {
    if (isNarrow()) return;
    var step = ev.shiftKey ? 24 : 10;
    if (kind === "left" || kind === "right") {
      if (ev.key === "ArrowLeft") {
        ev.preventDefault();
        nudgeHorizontal(kind, -step);
      } else if (ev.key === "ArrowRight") {
        ev.preventDefault();
        nudgeHorizontal(kind, step);
      }
    }
  }

  function bindHandle(id, kind) {
    var el = global.document.getElementById(id);
    if (!el) return;
    el.addEventListener("pointerdown", function (ev) {
      startDrag(kind, ev);
    });
    el.addEventListener("keydown", function (ev) {
      onKeyDown(kind, ev);
    });
    el.addEventListener("dblclick", function (ev) {
      ev.preventDefault();
      resetLayout();
    });
  }

  function bindResetButton() {
    var btn = global.document.getElementById("layout-reset-btn");
    if (!btn) return;
    btn.addEventListener("click", function () {
      resetLayout();
    });
  }

  function onBreakpointChange() {
    if (!rootEl) return;
    if (isNarrow()) clearInlineLayoutVars();
    else {
      var o = loadLayoutForApply();
      var n = normalizeLR(o.L, o.R);
      applyToDom(n.L, n.R);
      persist(n.L, n.R);
    }
  }

  function onWindowResize() {
    if (!rootEl || isNarrow()) return;
    if (winResizeTimer) global.clearTimeout(winResizeTimer);
    winResizeTimer = global.setTimeout(function () {
      winResizeTimer = null;
      var t = readPairFromComputed();
      var n = normalizeLR(t.L, t.R);
      applyToDom(n.L, n.R);
      persist(n.L, n.R);
    }, 120);
  }

  function init(options) {
    rootEl = (options && options.root) || global.document.querySelector("main.main--resizable");
    if (!rootEl) rootEl = global.document.querySelector("main.main");
    if (!rootEl) return;

    mqNarrow = global.matchMedia("(max-width: " + NARROW_MAX + "px)");
    if (mqNarrow.addEventListener) mqNarrow.addEventListener("change", onBreakpointChange);
    else if (mqNarrow.addListener) mqNarrow.addListener(onBreakpointChange);

    global.addEventListener("resize", onWindowResize);

    if (!isNarrow()) {
      global.requestAnimationFrame(function () {
        if (!rootEl || isNarrow()) return;
        var o = loadLayoutForApply();
        var n = normalizeLR(o.L, o.R);
        applyToDom(n.L, n.R);
        persist(n.L, n.R);
      });
    }

    bindHandle("resize-handle-left", "left");
    bindHandle("resize-handle-right", "right");
    bindResetButton();
  }

  global.RagxLayoutResizer = {
    init: init,
    resetLayout: resetLayout,
  };
})(typeof window !== "undefined" ? window : globalThis);
