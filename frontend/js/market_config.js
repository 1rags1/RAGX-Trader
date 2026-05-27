/**
 * Client mirror of GET /api/config — single source of truth for labels and symbol.
 * Populated by app.js via applyFromApi(); never guesses Binance US vs Global.
 */

(function (global) {
  "use strict";

  var state = {
    exchange: "binance",
    exchange_label: "Binance",
    instrument_type: "spot_crypto",
    instrument_label: "Spot crypto",
    instrument_category: "spot",
    margined_futures: false,
    symbol: "BTCUSDT",
    interval: "1m",
    binance_region: "com",
    rest_base_url: "",
    ws_base_url: "",
    ui_feed_label: "Binance Spot (Global)",
    ui_market_line: "Market: BTCUSDT · Binance Spot (Global)",
    market_badge: "Binance Spot (Global) • BTCUSDT",
  };

  function assertSpotContract() {
    if (state.instrument_category !== "spot" || state.margined_futures) {
      global.console.error(
        "[RAGX] Market contract violation: dashboard is Binance Spot only.",
        {
          instrument_category: state.instrument_category,
          margined_futures: state.margined_futures,
        }
      );
    }
  }

  function applyFromApi(c) {
    if (!c || typeof c !== "object") return;
    var k;
    for (k in state) {
      if (!Object.prototype.hasOwnProperty.call(state, k) || !Object.prototype.hasOwnProperty.call(c, k)) {
        continue;
      }
      var v = c[k];
      if (v === undefined || v === null) continue;
      if (typeof v === "string" && !String(v).trim()) continue;
      state[k] = v;
    }
    assertSpotContract();
  }

  function getSymbol() {
    return String(state.symbol || "BTCUSDT").toUpperCase();
  }

  function getInterval() {
    return state.interval || "1m";
  }

  function getUiFeedLabel() {
    return state.ui_feed_label || "Binance Spot";
  }

  function getMarketBadge() {
    if (state.market_badge && String(state.market_badge).trim()) {
      return String(state.market_badge);
    }
    var venue = state.binance_region === "us" ? "US" : "Global";
    return "Binance Spot (" + venue + ") • " + getSymbol();
  }

  function getMarketLine() {
    return state.ui_market_line || "Market: " + getSymbol() + " · " + getUiFeedLabel();
  }

  function getSubtitle() {
    var iv =
      global.RagxTimeframe && typeof global.RagxTimeframe.getCurrent === "function"
        ? global.RagxTimeframe.getCurrent()
        : getInterval();
    return getSymbol() + " · " + iv + " · " + getUiFeedLabel();
  }

  function getDataStatusLine() {
    return getSymbol() + " · " + getUiFeedLabel();
  }

  function chartAriaLabel() {
    return getSymbol() + " candlestick chart · " + getUiFeedLabel();
  }

  function documentTitle() {
    return "RAGX-Trader — " + getSymbol() + " · " + getUiFeedLabel();
  }

  function getState() {
    return state;
  }

  global.RagxMarket = {
    applyFromApi: applyFromApi,
    getSymbol: getSymbol,
    getInterval: getInterval,
    getUiFeedLabel: getUiFeedLabel,
    getMarketBadge: getMarketBadge,
    getMarketLine: getMarketLine,
    getSubtitle: getSubtitle,
    getDataStatusLine: getDataStatusLine,
    chartAriaLabel: chartAriaLabel,
    documentTitle: documentTitle,
    getState: getState,
  };
})(typeof window !== "undefined" ? window : globalThis);
