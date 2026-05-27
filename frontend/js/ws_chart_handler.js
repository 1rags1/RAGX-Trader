/**
 * WebSocket message router for chart dashboard (status, candles, timeframe, panels).
 */

(function (global) {
  "use strict";

  /**
   * @param {object} deps
   * @param {() => { chart: object, series: object } | null} deps.getChartApi
   * @param {(connected: boolean) => void} deps.setBinanceStatus
   * @param {(iso: string) => void} deps.setLastUpdate
   * @param {(c: object) => void} deps.fillDebug
   * @param {(bar: object, rawCandle?: object) => void} deps.onChartSyncedBar — bar + optional raw WS candle (is_final)
   * @param {(interval: string, bars: array) => void} deps.onTimeframeSync
   * @param {(snapshot: object) => void} deps.onIndicatorsSnapshot
   * @param {(strategyPayload: object) => void} deps.onStrategySnapshot
   */
  function createHandler(deps) {
    return function handleMessage(raw) {
      var msg;
      try {
        msg = JSON.parse(raw);
      } catch {
        return;
      }

      if (msg.type === "status") {
        deps.setBinanceStatus(msg.binance_connected);
        if (msg.last_update_utc) deps.setLastUpdate(msg.last_update_utc);
        return;
      }

      if (msg.type === "timeframe_changed" && msg.bars && Array.isArray(msg.bars)) {
        if (msg.last_update_utc) deps.setLastUpdate(msg.last_update_utc);
        if (msg.interval) global.RagxTimeframe.setCurrent(msg.interval);
        global.RagxTimeframe.syncToolbarPressed();
        deps.onTimeframeSync(msg.interval || global.RagxTimeframe.getCurrent(), msg.bars);
        return;
      }

      if (msg.type === "indicators" && msg.data) {
        if (msg.last_update_utc) deps.setLastUpdate(msg.last_update_utc);
        global.RagxIndicatorsPanel.render(msg.data);
        if (typeof deps.onIndicatorsSnapshot === "function") {
          deps.onIndicatorsSnapshot(msg.data);
        }
        return;
      }

      if (msg.type === "strategy" && msg.data) {
        if (msg.last_update_utc) deps.setLastUpdate(msg.last_update_utc);
        if (msg.last_update_utc && global.RagxStrategyPanel && global.RagxStrategyPanel.setDecisionTimestamp) {
          global.RagxStrategyPanel.setDecisionTimestamp(msg.last_update_utc);
        }
        global.RagxStrategyPanel.render(msg.data);
        if (typeof deps.onStrategySnapshot === "function") {
          deps.onStrategySnapshot(msg.data);
        }
        return;
      }

      if (msg.type === "candle" && msg.data) {
        var api = deps.getChartApi();
        if (!api) return;
        if (msg.last_update_utc) deps.setLastUpdate(msg.last_update_utc);
        deps.fillDebug(msg.data);
        // Kline stream only: header + paper sim use the same bar the chart accepted (not raw ticker).
        var upd = global.RagxChartUpdater.applyLiveCandle(api, msg.data);
        if (upd && upd.applied && upd.bar && typeof deps.onChartSyncedBar === "function") {
          deps.onChartSyncedBar(upd.bar, msg.data);
        }
        return;
      }

      if (msg.type === "error" && msg.message) {
        console.warn("ws:", msg.message);
      }
    };
  }

  global.RagxWsChartHandler = { createHandler };
})(typeof window !== "undefined" ? window : globalThis);
