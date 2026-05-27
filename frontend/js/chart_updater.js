/**
 * Chart updater — applies server bars to Lightweight Charts with correct bar spacing.
 */

(function (global) {
  "use strict";

  var RagxChart = global.RagxChart;

  /** Logical interval -> unix seconds per bar (must match backend.timeframes.INTERVAL_SECONDS). */
  var INTERVAL_TO_SECONDS = {
    "1m": 60,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "1d": 86400,
  };

  function barStepSeconds(interval) {
    return INTERVAL_TO_SECONDS[interval] || 60;
  }

  /**
   * Replace all candles (timeframe switch or initial load).
   * @param {{ chart: object, series: object }} chartApi
   */
  function replaceBars(chartApi, interval, bars) {
    if (!chartApi || !RagxChart) return null;
    RagxChart.configureBarStepSeconds(barStepSeconds(interval));
    if (chartApi.overlayCtl && typeof chartApi.overlayCtl.clear === "function") {
      chartApi.overlayCtl.clear();
    }
    if (chartApi.signalMarkerLayer && typeof chartApi.signalMarkerLayer.clear === "function") {
      chartApi.signalMarkerLayer.clear();
    }
    return RagxChart.setCandleData(chartApi.chart, chartApi.series, bars || []);
  }

  /**
   * Live kline tick — returns the bar applied to the series (or not applied) so the header matches the chart.
   */
  function applyLiveCandle(chartApi, candle) {
    if (!chartApi || !candle || !RagxChart) {
      return { applied: false, reason: "noop" };
    }
    return RagxChart.applyCandleUpdate(chartApi.chart, chartApi.series, candle);
  }

  global.RagxChartUpdater = {
    INTERVAL_TO_SECONDS,
    barStepSeconds,
    replaceBars,
    applyLiveCandle,
  };
})(typeof window !== "undefined" ? window : globalThis);
