/**
 * RAGX-Trader — TradingView Lightweight Charts
 *
 * 1m (and other) correctness:
 * - REST history: sort + dedupe by open time (last wins), NO synthetic gap-fill (avoids hundreds
 *   of fake 1m bars that normalizeBar would inflate → “stacked” tiny candles).
 * - Live: same open_time → series.update only (forming candle). New open_time → optional capped
 *   gap-fill with flat synthetic bars (no min-spread inflation).
 *
 * Debug: localStorage ragxCandleDebug=1 (verbose chart logs)
 *        localStorage ragxCandlePipeline=1 (history summary + live tick logs)
 *        localStorage ragxCandleAlignDebug=1 (first/last/active open time + UTC grid vs barStep)
 */

(function (global) {
  "use strict";

  var CENTRAL_TZ = "America/Chicago";
  var _barStepSec = 60;
  /** Cap synthetic bars per live jump (e.g. 720 ≈ 12h of 1m). */
  var MAX_LIVE_GAP_BARS = 720;

  var _didFit = false;
  var _stream = { lastBarTime: null, lastClose: null };

  function candleDebugEnabled() {
    try {
      return global.localStorage && global.localStorage.getItem("ragxCandleDebug") === "1";
    } catch {
      return false;
    }
  }

  function pipelineFrontEnabled() {
    try {
      return global.localStorage && global.localStorage.getItem("ragxCandlePipeline") === "1";
    } catch {
      return false;
    }
  }

  function alignDebugEnabled() {
    try {
      return global.localStorage && global.localStorage.getItem("ragxCandleAlignDebug") === "1";
    } catch {
      return false;
    }
  }

  function logAlign(msg, payload) {
    if (!alignDebugEnabled()) return;
    global.console.info("[RAGX candle_align]", msg, payload);
  }

  function dbg() {
    if (!candleDebugEnabled()) return;
    var a = Array.prototype.slice.call(arguments);
    a.unshift("[RAGX chart]");
    global.console.debug.apply(global.console, a);
  }

  function dbgWarn() {
    if (!candleDebugEnabled() && !pipelineFrontEnabled()) return;
    var a = Array.prototype.slice.call(arguments);
    a.unshift("[RAGX chart]");
    global.console.warn.apply(global.console, a);
  }

  function pipeLog() {
    if (!pipelineFrontEnabled()) return;
    var a = Array.prototype.slice.call(arguments);
    a.unshift("[RAGX pipeline]");
    global.console.info.apply(global.console, a);
  }

  function formatCentralAxisTime(time) {
    var ms;
    if (typeof time === "number") {
      ms = time * 1000;
    } else if (time && typeof time === "object" && "year" in time) {
      ms = Date.UTC(time.year, time.month - 1, time.day);
    } else {
      return "";
    }
    return new Intl.DateTimeFormat("en-US", {
      timeZone: CENTRAL_TZ,
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    }).format(new Date(ms));
  }

  function minSpreadForPrice(close) {
    var c = Math.abs(Number(close)) || 0;
    return Math.max(1, c * 1e-7);
  }

  /**
   * Exchange / history bars: enforce visible range for flat OHLC (dojis).
   */
  function normalizeBar(b) {
    var t = Math.floor(Number(b.time));
    var o = Number(b.open);
    var c = Number(b.close);
    var hIn = Number(b.high);
    var lIn = Number(b.low);
    var hi = Math.max(hIn, o, c);
    var lo = Math.min(lIn, o, c);
    var minS = minSpreadForPrice(c);
    if (hi - lo < minS) {
      var mid = (o + c) / 2;
      hi = mid + minS / 2;
      lo = mid - minS / 2;
    }
    hi = Math.max(hi, o, c);
    lo = Math.min(lo, o, c);
    return { time: t, open: o, high: hi, low: lo, close: c };
  }

  /** Gap-fill segments only: true flat, no artificial body height. */
  function normalizeBarFlat(time, price) {
    var t = Math.floor(Number(time));
    var v = Number(price);
    return { time: t, open: v, high: v, low: v, close: v };
  }

  function configureBarStepSeconds(sec) {
    var n = parseInt(sec, 10);
    _barStepSec = !Number.isNaN(n) && n > 0 ? n : 60;
  }

  /**
   * Sort ascending, dedupe by open time (last occurrence wins). Drop invalid rows.
   */
  function prepareBarsFromServer(bars) {
    if (!bars || !bars.length) return [];
    var byTime = {};
    for (var i = 0; i < bars.length; i++) {
      var b = bars[i];
      var t = Math.floor(Number(b.time));
      if (!Number.isFinite(t) || t <= 0) continue;
      var o = Number(b.open);
      var h = Number(b.high);
      var l = Number(b.low);
      var c = Number(b.close);
      if (![o, h, l, c].every(function (x) { return Number.isFinite(x) && x > 0; })) continue;
      if (h < l) continue;
      byTime[t] = { time: t, open: o, high: h, low: l, close: c };
    }
    var keys = Object.keys(byTime)
      .map(Number)
      .sort(function (a, b) {
        return a - b;
      });
    return keys.map(function (k) {
      return byTime[k];
    });
  }

  function resetStreamState() {
    _stream.lastBarTime = null;
    _stream.lastClose = null;
  }

  function createCandlestickChart(containerEl) {
    if (!global.LightweightCharts) {
      // If the external Lightweight Charts script fails to load (offline, CSP block, etc.),
      // avoid crashing the entire dashboard. Caller may choose to skip chart features.
      console.warn("LightweightCharts global not loaded; skipping chart init.");
      return { chart: null, series: null, ro: null };
    }

    const chart = global.LightweightCharts.createChart(containerEl, {
      localization: {
        locale: "en-US",
        timeFormatter: formatCentralAxisTime,
      },
      layout: {
        background: { type: "solid", color: "#0d1117" },
        textColor: "#8b949e",
      },
      grid: {
        vertLines: { color: "#21262d" },
        horzLines: { color: "#21262d" },
      },
      crosshair: {
        mode: global.LightweightCharts.CrosshairMode.Normal,
      },
      rightPriceScale: {
        borderColor: "#30363d",
        scaleMargins: {
          top: 0.1,
          bottom: 0.15,
        },
      },
      timeScale: {
        borderColor: "#30363d",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 6,
        tickMarkFormatter: function (time) {
          return formatCentralAxisTime(time);
        },
      },
    });

    const series = chart.addCandlestickSeries({
      upColor: "#3fb950",
      downColor: "#f85149",
      borderVisible: true,
      borderUpColor: "#238636",
      borderDownColor: "#da3633",
      wickUpColor: "#58d68d",
      wickDownColor: "#ff7b72",
      priceFormat: {
        type: "price",
        precision: 2,
        minMove: 0.01,
      },
    });

    const ro = new ResizeObserver(function () {
      var r = containerEl.getBoundingClientRect();
      chart.applyOptions({ width: Math.floor(r.width), height: Math.floor(r.height) });
    });
    ro.observe(containerEl);
    var rect = containerEl.getBoundingClientRect();
    chart.applyOptions({ width: Math.floor(rect.width), height: Math.floor(rect.height) });

    return { chart, series, ro };
  }

  /**
   * Same prepare + normalize path as setCandleData — use for header price / ref so it matches the chart tail.
   * @returns {{ lastOpenTime: number, lastClose: number, refClose: number } | null}
   */
  function tailForLiveDisplay(bars) {
    var prepared = prepareBarsFromServer(bars || []);
    if (!prepared.length) return null;
    var arr = prepared.map(normalizeBar);
    var last = arr[arr.length - 1];
    var refClose = arr.length >= 2 ? arr[arr.length - 2].close : last.open;
    return { lastOpenTime: last.time, lastClose: last.close, refClose: refClose };
  }

  function setCandleData(chart, series, bars) {
    resetStreamState();
    _didFit = false;

    series.setData([]);

    var prepared = prepareBarsFromServer(bars || []);
    if (!prepared.length) {
      dbg("setCandleData empty after prepare");
      return { lastBar: null };
    }

    var arr = prepared.map(normalizeBar);
    series.setData(arr);

    var last = arr[arr.length - 1];
    _stream.lastBarTime = last.time;
    _stream.lastClose = last.close;
    pipeLog("setCandleData", {
      count: arr.length,
      firstOpenTime: arr[0].time,
      lastOpenTime: last.time,
      barStepSec: _barStepSec,
    });
    dbg("setCandleData", { count: arr.length, lastOpenTime: last.time });
    if (alignDebugEnabled()) {
      var step = _barStepSec;
      var bad = [];
      var bi;
      for (bi = 0; bi < arr.length; bi++) {
        if (step > 0 && arr[bi].time % step !== 0) bad.push({ i: bi, t: arr[bi].time });
      }
      logAlign("setCandleData", {
        barStepSec: step,
        firstOpenTime: arr[0].time,
        lastOpenTime: last.time,
        activeCandleOpenTime: last.time,
        utcGridViolations: bad.length,
        violationSample: bad.slice(0, 5),
      });
    }

    _didFit = true;
    chart.timeScale().fitContent();
    return { lastBar: last };
  }

  function validateLiveCandle(candle) {
    var t = Math.floor(Number(candle.time));
    if (!Number.isFinite(t) || t <= 0) return null;
    var o = Number(candle.open);
    var h = Number(candle.high);
    var l = Number(candle.low);
    var c = Number(candle.close);
    if (![o, h, l, c].every(function (x) { return Number.isFinite(x) && x > 0; })) return null;
    if (h < l) return null;
    return { time: t, open: o, high: h, low: l, close: c, isFinal: candle.is_final === true };
  }

  /**
   * Apply one kline tick to the series. Returns the bar passed to series.update for the **active** candle
   * so the header can show the same close as the chart (skips stale/malformed ticks like the chart).
   * @returns {{ applied: true, bar: object } | { applied: false, reason: string }}
   */
  function applyCandleUpdate(chart, series, candle) {
    var parsed = validateLiveCandle(candle);
    if (!parsed) {
      dbgWarn("skip: malformed live candle", candle);
      return { applied: false, reason: "malformed" };
    }

    var prevLastOpen = _stream.lastBarTime;
    var t = parsed.time;
    var raw = {
      time: t,
      open: parsed.open,
      high: parsed.high,
      low: parsed.low,
      close: parsed.close,
    };
    var isFinal = parsed.isFinal;
    var barOut = normalizeBar(raw);

    if (_stream.lastBarTime === null) {
      series.update(barOut);
      _stream.lastBarTime = t;
      _stream.lastClose = raw.close;
      pipeLog("live first bar", { openTime: t, isFinal: isFinal, close: raw.close });
      dbg("new_candle (first)", { openTime: t, isFinal: isFinal, close: raw.close });
      logAlign("applyCandleUpdate:first", {
        barStepSec: _barStepSec,
        openTime: t,
        prevLastOpen: prevLastOpen,
        isFinal: isFinal,
        utcGridOk: _barStepSec <= 0 || t % _barStepSec === 0,
        mode: "first_bar",
      });
      if (!_didFit) {
        _didFit = true;
        chart.timeScale().fitContent();
      }
      return { applied: true, bar: barOut };
    }

    if (t < _stream.lastBarTime) {
      dbgWarn("skip: stale/out_of_order", { openTime: t, lastOpenTime: _stream.lastBarTime });
      pipeLog("live REJECT stale", { openTime: t, lastOpenTime: _stream.lastBarTime });
      return { applied: false, reason: "stale" };
    }

    if (t === _stream.lastBarTime) {
      series.update(barOut);
      _stream.lastClose = raw.close;
      pipeLog("live UPDATE same open_time", { openTime: t, isFinal: isFinal, close: raw.close });
      dbg("candle_update", { openTime: t, isFinal: isFinal, close: raw.close });
      logAlign("applyCandleUpdate:update_same", {
        barStepSec: _barStepSec,
        activeCandleOpenTime: t,
        prevLastOpen: prevLastOpen,
        isFinal: isFinal,
        utcGridOk: _barStepSec <= 0 || t % _barStepSec === 0,
        mode: "update_same_open_time",
      });
      if (!_didFit) {
        _didFit = true;
        chart.timeScale().fitContent();
      }
      return { applied: true, bar: barOut };
    }

    var delta = t - _stream.lastBarTime;
    if (_barStepSec > 0 && delta % _barStepSec !== 0) {
      dbgWarn("open_time jump not aligned to bar step", {
        openTime: t,
        lastOpenTime: _stream.lastBarTime,
        barStepSec: _barStepSec,
        delta: delta,
      });
    }

    if (t > _stream.lastBarTime + _barStepSec) {
      var pc = _stream.lastClose;
      var filled = 0;
      for (var x = _stream.lastBarTime + _barStepSec; x < t && filled < MAX_LIVE_GAP_BARS; x += _barStepSec) {
        series.update(normalizeBarFlat(x, pc));
        filled++;
        dbg("gap_fill", { openTime: x });
      }
      if (filled >= MAX_LIVE_GAP_BARS && _stream.lastBarTime + _barStepSec * (filled + 1) < t) {
        dbgWarn("gap_fill capped", { maxBars: MAX_LIVE_GAP_BARS, targetOpenTime: t });
        pipeLog("live gap_fill CAPPED", { filled: filled, nextOpenTime: t });
      }
    }

    series.update(barOut);
    _stream.lastBarTime = t;
    _stream.lastClose = raw.close;
    pipeLog("live NEW open_time", { openTime: t, isFinal: isFinal, close: raw.close });
    dbg("new_candle", { openTime: t, isFinal: isFinal, close: raw.close });
    logAlign("applyCandleUpdate:new_bar", {
      barStepSec: _barStepSec,
      openTime: t,
      prevLastOpen: prevLastOpen,
      deltaFromPrev: t - prevLastOpen,
      deltaModStep: _barStepSec > 0 ? (t - prevLastOpen) % _barStepSec : null,
      isFinal: isFinal,
      utcGridOk: _barStepSec <= 0 || t % _barStepSec === 0,
      mode: "new_open_time_after_gap_fill_if_any",
    });

    if (!_didFit) {
      _didFit = true;
      chart.timeScale().fitContent();
    }
    return { applied: true, bar: barOut };
  }

  function toBar(candle) {
    return normalizeBar({
      time: candle.time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    });
  }

  global.RagxChart = {
    createCandlestickChart,
    configureBarStepSeconds,
    setCandleData,
    applyCandleUpdate,
    tailForLiveDisplay,
    toBar,
    formatCentralAxisTime,
    CENTRAL_TZ,
  };
})(typeof window !== "undefined" ? window : globalThis);
