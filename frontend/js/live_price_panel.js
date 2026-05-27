/**
 * Live price header — same close as the chart's active candle (kline stream).
 * Uses RagxChart.tailForLiveDisplay / applyCandleUpdate results only — no separate ticker.
 */

(function (global) {
  "use strict";

  var els = null;
  var lastOpenTime = null;
  var lastCloseValue = null;
  var refClose = null;
  /** @type {"kline"|"trade"} */
  var sourceMode = "kline";

  var MODE_LABELS = {
    kline: "Active candle close",
    trade: "Last trade price",
  };

  function fmtPrice(n) {
    if (Number.isNaN(n)) return "—";
    return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toFixed(2);
  }

  function setDir(el, dir) {
    if (!el) return;
    el.dataset.dir = dir;
  }

  function renderModeLabel() {
    if (!els || !els.mode) return;
    els.mode.textContent = MODE_LABELS[sourceMode] || MODE_LABELS.kline;
  }

  function setPriceSourceMode(mode) {
    if (mode === "trade") sourceMode = "trade";
    else sourceMode = "kline";
    renderModeLabel();
  }

  function renderChange(delta, pct) {
    if (!els || !els.change) return;
    if (refClose === null || lastCloseValue === null || Number.isNaN(delta)) {
      els.change.textContent = "—";
      setDir(els.change, "flat");
      return;
    }
    if (Math.abs(delta) < 1e-8 && Math.abs(pct) < 1e-6) {
      els.change.textContent = "0.00 (0.00%)";
      setDir(els.change, "flat");
      return;
    }
    var sign = delta > 0 ? "+" : "";
    var psign = pct > 0 ? "+" : "";
    els.change.textContent =
      sign + fmtPrice(delta) + " (" + psign + pct.toFixed(2) + "%)";
    setDir(els.change, delta > 0 ? "up" : delta < 0 ? "down" : "flat");
  }

  function renderPrice(close) {
    if (!els || !els.value) return;
    els.value.textContent = fmtPrice(close);
    var delta = close - refClose;
    var pct = refClose !== null && Math.abs(refClose) > 1e-12 ? (delta / refClose) * 100 : 0;
    renderChange(delta, pct);
    if (global.RagxStrategyPanel && typeof global.RagxStrategyPanel.refreshDecisionMeta === "function") {
      global.RagxStrategyPanel.refreshDecisionMeta();
    }
  }

  function bind(elements) {
    els = elements;
    renderModeLabel();
  }

  /**
   * After replaceBars / history load: same tail as chart (prepareBarsFromServer + normalizeBar).
   */
  function resetFromBars(bars) {
    var RagxChart = global.RagxChart;
    var tail = RagxChart && typeof RagxChart.tailForLiveDisplay === "function" ? RagxChart.tailForLiveDisplay(bars) : null;
    if (!tail) {
      lastOpenTime = null;
      lastCloseValue = null;
      refClose = null;
      if (els && els.value) els.value.textContent = "—";
      if (els && els.change) {
        els.change.textContent = "—";
        setDir(els.change, "flat");
      }
      return;
    }
    lastOpenTime = tail.lastOpenTime;
    lastCloseValue = tail.lastClose;
    refClose = tail.refClose;
    renderPrice(tail.lastClose);
  }

  /**
   * One live tick: `bar` is the normalized OHLC object passed to series.update (chart pipeline).
   */
  function onChartSyncedBar(bar) {
    if (!bar || bar.close === undefined || bar.close === null) return;
    var t = Math.floor(Number(bar.time));
    var close = Number(bar.close);
    if (Number.isNaN(close)) return;

    if (lastOpenTime !== null && !Number.isNaN(t) && t !== lastOpenTime) {
      refClose = lastCloseValue;
    }
    lastOpenTime = Number.isNaN(t) ? lastOpenTime : t;
    lastCloseValue = close;
    if (refClose === null) {
      var op = Number(bar.open);
      refClose = Number.isNaN(op) ? close : op;
    }
    renderPrice(close);
  }

  function setLastUpdateText(text) {
    if (!els || !els.updated) return;
    els.updated.textContent = text && String(text).length ? "Last update: " + text : "Last update: —";
  }

  /** For market diagnostics — last values shown in the header after chart sync. */
  function getDiagnosticsSnapshot() {
    return {
      displayedClose: lastCloseValue,
      lastOpenTimeUnix: lastOpenTime,
    };
  }

  global.RagxLivePrice = {
    bind,
    resetFromBars,
    onChartSyncedBar,
    setLastUpdateText,
    setPriceSourceMode,
    getDiagnosticsSnapshot,
  };
})(typeof window !== "undefined" ? window : globalThis);
