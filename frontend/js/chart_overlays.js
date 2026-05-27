/**
 * Single trend line: EMA(20) on close from `snapshot.chart_overlays.lines.ema_20`.
 * Subtle line; legend text lives in HTML (#chart-trend-legend).
 */

(function (global) {
  "use strict";

  var LC = global.LightweightCharts;

  var LINE_KEY = "ema_20";

  var TREND_LINE_STYLE = {
    color: "rgba(91, 155, 212, 0.92)",
    lineWidth: 1.5,
    lastValueVisible: false,
    priceLineVisible: false,
    crosshairMarkerVisible: false,
  };

  function normalizePoints(arr) {
    if (!arr || !arr.length) return [];
    var out = [];
    for (var i = 0; i < arr.length; i++) {
      var p = arr[i];
      if (!p) continue;
      var t = Math.floor(Number(p.time));
      var v = Number(p.value);
      if (!Number.isFinite(t) || t <= 0) continue;
      if (!Number.isFinite(v) || v <= 0) continue;
      out.push({ time: t, value: v });
    }
    return out;
  }

  function createOverlayController(chart) {
    if (!LC || !chart) {
      return {
        applyFromIndicatorSnapshot: function () {},
        clear: function () {},
        mountToggleBar: function () {},
      };
    }

    var series = chart.addLineSeries(Object.assign({}, TREND_LINE_STYLE));

    return {
      applyFromIndicatorSnapshot: function (snapshot) {
        if (!snapshot || typeof snapshot !== "object") return;
        var co = snapshot.chart_overlays;
        if (!co || !co.lines) return;
        var pts = normalizePoints(co.lines[LINE_KEY] || []);
        series.setData(pts);
      },

      clear: function () {
        series.setData([]);
      },

      mountToggleBar: function () {},
    };
  }

  global.RagxChartOverlays = {
    TREND_LINE_KEY: LINE_KEY,
    createOverlayController: createOverlayController,
    normalizePoints: normalizePoints,
  };
})(typeof window !== "undefined" ? window : globalThis);
