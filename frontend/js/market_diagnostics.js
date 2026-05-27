/**
 * Internal market diagnostics — collapsible, off by default.
 * Compares header price to last chart-synced kline close (should match).
 */

(function (global) {
  "use strict";

  /** Absolute diff above this triggers a warning (same float path should be ~0). */
  var DIFF_WARN_ABS = 0.0001;

  var lastKlineOpenTime = null;
  var lastKlineClose = null;
  var lastIsFinal = null;
  var lastSource = "none";

  function fmtPrice(n) {
    if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
    var x = Number(n);
    return Math.abs(x) >= 1000 ? x.toLocaleString(undefined, { maximumFractionDigits: 8 }) : String(x);
  }

  function fmtUnix(ts) {
    if (ts == null || !Number.isFinite(Number(ts))) return "—";
    var t = Math.floor(Number(ts));
    var axis =
      global.RagxChart && typeof global.RagxChart.formatCentralAxisTime === "function"
        ? global.RagxChart.formatCentralAxisTime(t)
        : "";
    return (axis ? axis + " · " : "") + String(t) + "s";
  }

  function candleStateLabel() {
    if (lastIsFinal === true) return "Closed (final)";
    if (lastIsFinal === false) return "Open (forming)";
    if (lastSource === "history") return "Unknown (history payload has no is_final)";
    return "—";
  }

  function onChartSyncedBar(bar, raw) {
    if (!bar) return;
    lastSource = "live";
    lastKlineOpenTime = Math.floor(Number(bar.time));
    lastKlineClose = Number(bar.close);
    if (raw && typeof raw === "object") {
      if (raw.is_final === true) lastIsFinal = true;
      else if (raw.is_final === false) lastIsFinal = false;
      else lastIsFinal = null;
    } else {
      lastIsFinal = null;
    }
    render();
  }

  function onBarsLoaded(bars) {
    var tail =
      global.RagxChart && typeof global.RagxChart.tailForLiveDisplay === "function"
        ? global.RagxChart.tailForLiveDisplay(bars)
        : null;
    if (!tail) {
      lastKlineOpenTime = null;
      lastKlineClose = null;
      lastIsFinal = null;
      lastSource = "none";
      render();
      return;
    }
    lastSource = "history";
    lastKlineOpenTime = tail.lastOpenTime;
    lastKlineClose = tail.lastClose;
    lastIsFinal = null;
    render();
  }

  function refreshFromConfig() {
    render();
  }

  function consistencyBlock() {
    var lp =
      global.RagxLivePrice && typeof global.RagxLivePrice.getDiagnosticsSnapshot === "function"
        ? global.RagxLivePrice.getDiagnosticsSnapshot()
        : {};
    var headerClose = lp.displayedClose;
    var klineClose = lastKlineClose;
    var diff =
      headerClose != null && klineClose != null && Number.isFinite(headerClose) && Number.isFinite(klineClose)
        ? Math.abs(headerClose - klineClose)
        : null;
    var warn = diff != null && diff > DIFF_WARN_ABS;
    return {
      headerClose: headerClose,
      klineClose: klineClose,
      diff: diff,
      warn: warn,
    };
  }

  function clearBody(root) {
    while (root.firstChild) root.removeChild(root.firstChild);
  }

  function addRow(root, label, value, valueClass) {
    var dt = global.document.createElement("dt");
    dt.className = "market-diagnostics-dt";
    dt.textContent = label;
    var dd = global.document.createElement("dd");
    dd.className = "market-diagnostics-dd" + (valueClass ? " " + valueClass : "");
    dd.textContent = value;
    root.appendChild(dt);
    root.appendChild(dd);
  }

  function render() {
    var root = global.document.getElementById("market-diagnostics-body");
    if (!root) return;

    var keepCompareOpen = false;
    var prevCmp = root.querySelector("details.market-diagnostics-compare");
    if (prevCmp && prevCmp.open) keepCompareOpen = true;

    var M = global.RagxMarket && global.RagxMarket.getState ? global.RagxMarket.getState() : {};
    var tf =
      global.RagxTimeframe && typeof global.RagxTimeframe.getCurrent === "function"
        ? global.RagxTimeframe.getCurrent()
        : M.interval || "—";

    clearBody(root);

    addRow(
      root,
      "Exchange",
      String(M.exchange_label || "—") + (M.exchange ? " · " + String(M.exchange) : "")
    );
    addRow(root, "Symbol", String(M.symbol || "—"));
    addRow(root, "Instrument", String(M.instrument_label || M.instrument_type || "—"));
    addRow(
      root,
      "Spot vs futures",
      String(M.instrument_category || "—") +
        (M.margined_futures ? " — margined futures (unexpected)" : " — not margined futures")
    );
    var badgeFallback =
      global.RagxMarket && typeof global.RagxMarket.getMarketBadge === "function"
        ? global.RagxMarket.getMarketBadge()
        : "—";
    addRow(root, "Market badge", String(M.market_badge || badgeFallback));
    addRow(root, "Timeframe", String(tf));
    addRow(root, "WebSocket base", String(M.ws_base_url || "—"));
    addRow(root, "REST base", String(M.rest_base_url || "—"));
    addRow(root, "Latest kline open", fmtUnix(lastKlineOpenTime));
    addRow(root, "Latest kline close (chart)", fmtPrice(lastKlineClose));

    var snap = global.RagxLivePrice && global.RagxLivePrice.getDiagnosticsSnapshot
      ? global.RagxLivePrice.getDiagnosticsSnapshot()
      : {};
    addRow(root, "Header price (displayed)", fmtPrice(snap.displayedClose));
    addRow(root, "Candle state", candleStateLabel());

    var c = consistencyBlock();
    var consText = "—";
    var consClass = "";
    if (c.diff != null && Number.isFinite(c.diff)) {
      consText = c.warn
        ? "WARN — |Δ| = " + fmtPrice(c.diff) + " (threshold " + String(DIFF_WARN_ABS) + ")"
        : "OK — |Δ| = " + fmtPrice(c.diff);
      consClass = c.warn ? "market-diagnostics-warn" : "market-diagnostics-ok";
    }
    addRow(root, "Consistency (header vs chart kline close)", consText, consClass);

    var note = global.document.createElement("p");
    note.className = "market-diagnostics-note";
    note.textContent =
      "Internal debugging only. Prices use the Binance Spot kline stream and the same bar the chart applies.";
    root.appendChild(note);

    var compare = global.document.createElement("details");
    compare.className = "market-diagnostics-compare";
    var cmpSum = global.document.createElement("summary");
    cmpSum.className = "market-diagnostics-compare-summary";
    cmpSum.textContent = "Compare mode (external charts or brokers)";
    compare.appendChild(cmpSum);
    var cmpInner = global.document.createElement("div");
    cmpInner.className = "market-diagnostics-compare-inner";
    var cmpLead = global.document.createElement("p");
    cmpLead.className = "market-diagnostics-compare-lead";
    cmpLead.textContent =
      "Compare this app only to the same exchange, symbol, and instrument type. Otherwise price differences are expected — not a bug in this dashboard.";
    cmpInner.appendChild(cmpLead);
    var cmpUl = global.document.createElement("ul");
    cmpUl.className = "market-diagnostics-compare-list";
    var examples = [
      "Binance Spot BTCUSDT vs CME BTC futures — different venue and contract; will not match.",
      "Binance US vs Binance Global — both Spot, but order books differ; prices can diverge.",
      "Spot vs perpetual futures — different instruments and often different funding/mark; candles and last price can differ.",
    ];
    var ex;
    for (ex = 0; ex < examples.length; ex++) {
      var li = global.document.createElement("li");
      li.textContent = examples[ex];
      cmpUl.appendChild(li);
    }
    cmpInner.appendChild(cmpUl);
    compare.appendChild(cmpInner);
    compare.open = keepCompareOpen;
    root.appendChild(compare);
  }

  var pollId = null;

  function mount() {
    var det = global.document.getElementById("market-diagnostics");
    if (!det) return;
    det.addEventListener("toggle", function () {
      if (det.open) {
        render();
        if (pollId) global.clearInterval(pollId);
        pollId = global.setInterval(render, 1000);
      } else {
        if (pollId) {
          global.clearInterval(pollId);
          pollId = null;
        }
      }
    });
  }

  global.RagxMarketDiagnostics = {
    onChartSyncedBar: onChartSyncedBar,
    onBarsLoaded: onBarsLoaded,
    refreshFromConfig: refreshFromConfig,
    mount: mount,
    render: render,
  };
})(typeof window !== "undefined" ? window : globalThis);
