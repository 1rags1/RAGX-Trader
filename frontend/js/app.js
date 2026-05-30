/**
 * RAGX-Trader — browser entry
 *
 * Timeframe switching: RagxTimeframe + POST /api/timeframe; server restarts Binance WS
 * and broadcasts timeframe_changed. Chart spacing via RagxChartUpdater + RagxChart.
 */

(function () {
  "use strict";

  const els = {
    headerMarketBadge: document.getElementById("header-market-badge"),
    subtitleFeed: document.getElementById("subtitle-feed"),
    livePriceValue: document.getElementById("live-price-value"),
    livePriceChange: document.getElementById("live-price-change"),
    livePriceUpdated: document.getElementById("live-price-updated"),
    livePriceMarket: document.getElementById("live-price-market"),
    statusMarketSource: document.getElementById("status-market-source"),
    dotBackend: document.getElementById("dot-backend"),
    labelBackend: document.getElementById("label-backend"),
    dotBinance: document.getElementById("dot-binance"),
    labelBinance: document.getElementById("label-binance"),
    lastUpdate: document.getElementById("label-last-update"),
    dbgOpen: document.getElementById("dbg-open"),
    dbgHigh: document.getElementById("dbg-high"),
    dbgLow: document.getElementById("dbg-low"),
    dbgClose: document.getElementById("dbg-close"),
    dbgVolume: document.getElementById("dbg-volume"),
    chartToolbar: document.getElementById("chart-toolbar"),
    chartContainer: document.getElementById("chart-container"),
  };

  let ws = null;
  let chartApi = null;
  let heartbeatId = null;
  let previousConfigSymbol = null;
  let sessionExpired = false;

  const ui = {
    chartOverlay: document.getElementById("chart-loading-overlay"),
    chartOverlayText: document.getElementById("chart-loading-text"),
    sessionExpiredBanner: document.getElementById("session-expired-banner"),
  };

  function setChartLoading(loading, text) {
    if (!ui.chartOverlay) return;
    ui.chartOverlay.hidden = !loading;
    if (ui.chartOverlayText && text) ui.chartOverlayText.textContent = text;
  }

  function showSessionExpired() {
    sessionExpired = true;
    if (ui.sessionExpiredBanner) ui.sessionExpiredBanner.hidden = false;
    setChartLoading(true, "Session expired. Redirecting to access gate…");
    window.setTimeout(() => {
      window.location.href = "/gate";
    }, 1200);
  }

  function setBackendStatus(connected) {
    els.dotBackend.dataset.state = connected ? "on" : "off";
    els.labelBackend.textContent = connected ? "Connected" : "Disconnected";
    if (window.RagxChatPanel && typeof window.RagxChatPanel.updateContext === "function") {
      window.RagxChatPanel.updateContext({ backendConnected: !!connected });
    }
  }

  function setBinanceStatus(connected) {
    if (connected === null || connected === undefined) {
      els.dotBinance.dataset.state = "unknown";
      els.labelBinance.textContent = "…";
      return;
    }
    els.dotBinance.dataset.state = connected ? "on" : "off";
    els.labelBinance.textContent = connected ? "Live" : "Off";
    if (window.RagxChatPanel && typeof window.RagxChatPanel.updateContext === "function") {
      window.RagxChatPanel.updateContext({ feedConnected: !!connected });
    }
  }

  function setLastUpdate(iso) {
    let shown = "—";
    if (iso && String(iso).length) {
      try {
        const d = new Date(iso);
        if (!Number.isNaN(d.getTime())) {
          shown = new Intl.DateTimeFormat("en-US", {
            timeZone: "America/Chicago",
            month: "short",
            day: "numeric",
            year: "numeric",
            hour: "numeric",
            minute: "2-digit",
            second: "2-digit",
            hour12: true,
            timeZoneName: "short",
          }).format(d);
        } else {
          shown = iso;
        }
      } catch {
        shown = iso;
      }
    }
    if (els.lastUpdate) els.lastUpdate.textContent = shown;
    RagxLivePrice.setLastUpdateText(shown === "—" ? "" : shown);
    if (window.RagxChatPanel && typeof window.RagxChatPanel.updateContext === "function") {
      window.RagxChatPanel.updateContext({ lastUpdateIso: iso || null });
    }
  }

  function fillDebug(candle) {
    const fmt = (v) => (v === undefined || v === null ? "—" : String(v));
    els.dbgOpen.textContent = fmt(candle.open);
    els.dbgHigh.textContent = fmt(candle.high);
    els.dbgLow.textContent = fmt(candle.low);
    els.dbgClose.textContent = fmt(candle.close);
    els.dbgVolume.textContent = fmt(candle.volume);
  }

  function applyMarketLabelsFromConfig(c) {
    const M = window.RagxMarket;
    if (M && typeof M.applyFromApi === "function") {
      M.applyFromApi(c);
    }
    if (els.headerMarketBadge && M && typeof M.getMarketBadge === "function") {
      els.headerMarketBadge.textContent = M.getMarketBadge();
    }
    if (els.subtitleFeed && M && typeof M.getSubtitle === "function") {
      els.subtitleFeed.textContent = M.getSubtitle();
    }
    if (els.livePriceMarket && M && typeof M.getMarketLine === "function") {
      els.livePriceMarket.textContent = M.getMarketLine();
    }
    if (els.statusMarketSource && M && typeof M.getDataStatusLine === "function") {
      els.statusMarketSource.textContent = M.getDataStatusLine();
    }
    if (M && typeof M.chartAriaLabel === "function" && els.chartContainer) {
      els.chartContainer.setAttribute("aria-label", M.chartAriaLabel());
    }
    if (M && typeof M.documentTitle === "function") {
      document.title = M.documentTitle();
    }
    if (window.RagxMarketDiagnostics && typeof window.RagxMarketDiagnostics.refreshFromConfig === "function") {
      window.RagxMarketDiagnostics.refreshFromConfig();
    }
    if (window.RagxChatPanel && typeof window.RagxChatPanel.updateContext === "function") {
      window.RagxChatPanel.updateContext({
        market: String(c.symbol || "BTCUSDT").toUpperCase(),
        interval: String(c.interval || RagxTimeframe.getCurrent() || "1m"),
      });
    }
  }

  async function fetchConfig() {
    try {
      const r = await fetch("/api/config");
      if (!r.ok) return;
      const c = await r.json();
      const M = window.RagxMarket;
      const sym = String(c.symbol || (M && M.getSymbol ? M.getSymbol() : "") || "BTCUSDT").toUpperCase();
      if (previousConfigSymbol !== null && sym !== previousConfigSymbol && chartApi) {
        if (chartApi.signalMarkerLayer) chartApi.signalMarkerLayer.clear();
      }
      previousConfigSymbol = sym;
      if (chartApi && chartApi.signalMarkerLayer && typeof chartApi.signalMarkerLayer.setSymbol === "function") {
        chartApi.signalMarkerLayer.setSymbol(sym);
      }
      if (c.interval) RagxTimeframe.setCurrent(c.interval);
      RagxTimeframe.syncToolbarPressed();
      applyMarketLabelsFromConfig(c);
    } catch {
      const M = window.RagxMarket;
      if (els.headerMarketBadge && M && typeof M.getMarketBadge === "function") {
        els.headerMarketBadge.textContent = M.getMarketBadge();
      }
      if (els.subtitleFeed && M && typeof M.getSubtitle === "function") {
        els.subtitleFeed.textContent = M.getSubtitle();
      } else if (els.subtitleFeed) {
        els.subtitleFeed.textContent = `BTCUSDT · ${RagxTimeframe.getCurrent()} · …`;
      }
      if (els.livePriceMarket && M && typeof M.getMarketLine === "function") {
        els.livePriceMarket.textContent = M.getMarketLine();
      }
      if (els.statusMarketSource && M && typeof M.getDataStatusLine === "function") {
        els.statusMarketSource.textContent = M.getDataStatusLine();
      }
    }
  }

  function wsUrl() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}/ws/chart`;
  }

  function focusChartAtUnixTime(unixSec) {
    if (!chartApi || !chartApi.chart) return;
    const t = Math.floor(Number(unixSec));
    if (!Number.isFinite(t) || t <= 0) return;
    const iv = RagxTimeframe.getCurrent();
    const step = RagxChartUpdater.barStepSeconds(iv) || 60;
    const span = step * 16;
    const from = t - Math.floor(span * 0.28);
    const to = t + Math.floor(span * 0.72);
    try {
      chartApi.chart.timeScale().setVisibleRange({ from, to });
    } catch {
      try {
        chartApi.chart.timeScale().fitContent();
      } catch {
        /* ignore */
      }
    }
  }

  async function loadChartHistory() {
    if (!chartApi) return;
    const iv = RagxTimeframe.getCurrent();
    try {
      const r = await fetch(`/api/candles/history?interval=${encodeURIComponent(iv)}&limit=200`);
      if (!r.ok) return;
      const j = await r.json();
      const bars = j.bars;
      if (Array.isArray(bars)) {
        RagxChartUpdater.replaceBars(chartApi, j.interval || iv, bars);
        RagxLivePrice.resetFromBars(bars);
        setChartLoading(false);
        if (window.RagxMarketDiagnostics && typeof window.RagxMarketDiagnostics.onBarsLoaded === "function") {
          window.RagxMarketDiagnostics.onBarsLoaded(bars);
        }
      } else {
        setChartLoading(true, "No chart data yet. Waiting for feed…");
      }
    } catch {
      setChartLoading(true, "Waiting for live feed…");
    }
  }

  function onTimeframeSync(interval, bars) {
    if (!chartApi) return;
    RagxTimeframe.setCurrent(interval);
    RagxTimeframe.syncToolbarPressed();
    void fetchConfig();
    RagxChartUpdater.replaceBars(chartApi, interval, bars);
    if (Array.isArray(bars)) {
      RagxLivePrice.resetFromBars(bars);
      if (window.RagxMarketDiagnostics && typeof window.RagxMarketDiagnostics.onBarsLoaded === "function") {
        window.RagxMarketDiagnostics.onBarsLoaded(bars);
      }
    }
    if (RagxStrategyPanel && typeof RagxStrategyPanel.refreshDecisionMeta === "function") {
      RagxStrategyPanel.refreshDecisionMeta();
    }
    if (RagxRecentSignalHistory && typeof RagxRecentSignalHistory.scheduleRefresh === "function") {
      RagxRecentSignalHistory.scheduleRefresh();
    }
  }

  function applyIndicatorOverlays(snapshot) {
    if (!chartApi || !chartApi.overlayCtl) return;
    chartApi.overlayCtl.applyFromIndicatorSnapshot(snapshot);
    if (RagxPaperTrading && typeof RagxPaperTrading.cacheIndicators === "function") {
      RagxPaperTrading.cacheIndicators(snapshot);
    }
    if (window.RagxChatPanel && typeof window.RagxChatPanel.updateContext === "function") {
      window.RagxChatPanel.updateContext({ indicators: snapshot || null });
    }
  }

  function applyStrategyChartVisuals(strategyPayload) {
    if (!chartApi || !chartApi.signalMarkerLayer) return;
    chartApi.signalMarkerLayer.apply(strategyPayload);
    if (window.RagxChatPanel && typeof window.RagxChatPanel.updateContext === "function") {
      window.RagxChatPanel.updateContext({ strategy: strategyPayload || null });
    }
  }

  function onStrategySnapshotForWs(strategyPayload) {
    applyStrategyChartVisuals(strategyPayload);
    if (RagxPaperTrading && typeof RagxPaperTrading.onStrategy === "function") {
      RagxPaperTrading.onStrategy(strategyPayload);
    }
    if (RagxRecentSignalHistory && typeof RagxRecentSignalHistory.scheduleRefresh === "function") {
      RagxRecentSignalHistory.scheduleRefresh();
    }
    if (window.RagxSignalAlerts && typeof window.RagxSignalAlerts.processStrategySnapshot === "function") {
      window.RagxSignalAlerts.processStrategySnapshot(strategyPayload);
    }
  }

  const handleMessage = RagxWsChartHandler.createHandler({
    getChartApi: () => chartApi,
    setBinanceStatus,
    setLastUpdate,
    fillDebug,
    onChartSyncedBar: (bar, raw) => {
      if (RagxPaperTrading && typeof RagxPaperTrading.onCandle === "function") {
        RagxPaperTrading.onCandle(bar);
      }
      RagxLivePrice.onChartSyncedBar(bar);
      if (window.RagxMarketDiagnostics && typeof window.RagxMarketDiagnostics.onChartSyncedBar === "function") {
        window.RagxMarketDiagnostics.onChartSyncedBar(bar, raw);
      }
      if (window.RagxChatPanel && typeof window.RagxChatPanel.updateContext === "function") {
        window.RagxChatPanel.updateContext({ lastCandle: bar || null });
      }
    },
    onTimeframeSync,
    onIndicatorsSnapshot: applyIndicatorOverlays,
    onStrategySnapshot: onStrategySnapshotForWs,
  });

  async function fetchSnapshotsRest() {
    try {
      const [indRes, stratRes] = await Promise.all([fetch("/api/indicators"), fetch("/api/strategy")]);
      if (indRes.ok) {
        const data = await indRes.json();
        RagxIndicatorsPanel.render(data);
        applyIndicatorOverlays(data);
      }
      if (stratRes.ok) {
        const s = await stratRes.json();
        RagxStrategyPanel.render(s);
        applyStrategyChartVisuals(s);
        if (RagxPaperTrading && typeof RagxPaperTrading.onStrategy === "function") {
          RagxPaperTrading.onStrategy(s);
        }
        if (RagxRecentSignalHistory && typeof RagxRecentSignalHistory.scheduleRefresh === "function") {
          RagxRecentSignalHistory.scheduleRefresh();
        }
        if (window.RagxSignalAlerts && typeof window.RagxSignalAlerts.processStrategySnapshot === "function") {
          window.RagxSignalAlerts.processStrategySnapshot(s);
        }
      }
    } catch {
      /* WS will still stream */
    }
  }

  function connect() {
    setBackendStatus(false);
    ws = new WebSocket(wsUrl());

    ws.onopen = () => {
      if (sessionExpired) return;
      setBackendStatus(true);
      if (heartbeatId) window.clearInterval(heartbeatId);
      heartbeatId = window.setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 25000);
      void fetchConfig();
      void loadChartHistory();
      fetchSnapshotsRest();
    };

    ws.onclose = () => {
      if (heartbeatId) window.clearInterval(heartbeatId);
      heartbeatId = null;
      setBackendStatus(false);
      setBinanceStatus(false);
      if (ws && ws.code === 4401) {
        showSessionExpired();
        return;
      }
      setChartLoading(true, "Reconnecting to market stream…");
      window.setTimeout(connect, 2000);
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onmessage = (ev) => handleMessage(ev.data);
  }

  async function boot() {
    setChartLoading(true, "Loading chart engine…");
    if (window.RagxLayoutResizer && typeof window.RagxLayoutResizer.init === "function") {
      window.RagxLayoutResizer.init({ root: document.querySelector("main.main") });
    }
    if (window.RagxSignalAlerts && typeof window.RagxSignalAlerts.init === "function") {
      window.RagxSignalAlerts.init();
    }
    if (window.RagxChatPanel && typeof window.RagxChatPanel.mount === "function") {
      window.RagxChatPanel.mount();
    }
    if (RagxPaperTrading && typeof RagxPaperTrading.init === "function") {
      RagxPaperTrading.init();
    }
    if (RagxBacktestPanel && typeof RagxBacktestPanel.mount === "function") {
      RagxBacktestPanel.mount();
    }
    RagxLivePrice.bind({
      value: els.livePriceValue,
      change: els.livePriceChange,
      updated: els.livePriceUpdated,
      mode: document.getElementById("live-price-mode"),
    });
    if (RagxLivePrice.setPriceSourceMode) {
      RagxLivePrice.setPriceSourceMode("kline");
    }
    try {
      chartApi = RagxChart.createCandlestickChart(els.chartContainer);
    } catch {
      chartApi = null;
    }

    // If Lightweight Charts failed to load (e.g., unpkg blocked), don't crash boot().
    if (!chartApi || !chartApi.chart || !chartApi.series) {
      if (els.subtitleFeed) {
        els.subtitleFeed.textContent =
          "Chart library failed to load (check internet/CSP for unpkg). Investor tab still works.";
      }
      setChartLoading(true, "Chart failed to load.");
      return;
    }

    if (window.RagxChartOverlays && typeof window.RagxChartOverlays.createOverlayController === "function") {
      chartApi.overlayCtl = window.RagxChartOverlays.createOverlayController(chartApi.chart);
    }
    RagxStrategyPanel.setChartFocusHandler(focusChartAtUnixTime);
    if (window.RagxSignalMarkers && typeof window.RagxSignalMarkers.createLayer === "function") {
      chartApi.signalMarkerLayer = window.RagxSignalMarkers.createLayer(chartApi.chart, chartApi.series, els.chartContainer, {
        onMarkerSelect: function (detail) {
          RagxStrategyPanel.highlightSignalIds(detail.signalIds || []);
        },
        onMarkerDeselect: function () {
          RagxStrategyPanel.highlightSignalIds([]);
        },
      });
      RagxStrategyPanel.setMarkerHighlightHandler(function (ids) {
        if (chartApi.signalMarkerLayer && typeof chartApi.signalMarkerLayer.setHighlightSignalIds === "function") {
          chartApi.signalMarkerLayer.setHighlightSignalIds(ids || []);
        }
      });
    }
    if (RagxRecentSignalHistory && typeof RagxRecentSignalHistory.mount === "function") {
      RagxRecentSignalHistory.mount();
    }
    if (window.RagxMarketDiagnostics && typeof window.RagxMarketDiagnostics.mount === "function") {
      window.RagxMarketDiagnostics.mount();
    }
    RagxTimeframe.mountToolbar(els.chartToolbar, function (iv, bars) {
      RagxChartUpdater.replaceBars(chartApi, iv, bars);
      if (Array.isArray(bars)) {
        RagxLivePrice.resetFromBars(bars);
        if (window.RagxMarketDiagnostics && typeof window.RagxMarketDiagnostics.onBarsLoaded === "function") {
          window.RagxMarketDiagnostics.onBarsLoaded(bars);
        }
      }
      void fetchConfig();
    });
    await fetchConfig();
    await loadChartHistory();
    fetchSnapshotsRest();
    connect();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      void boot();
    });
  } else {
    void boot();
  }
})();
