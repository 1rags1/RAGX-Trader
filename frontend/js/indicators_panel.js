/**
 * RAGX-Trader — indicators side panel (numbers + plain-English summaries)
 *
 * Advanced readings stay here; the chart stays minimal (candles + EMA + stickers).
 */

(function (global) {
  "use strict";

  function el(id) {
    return document.getElementById(id);
  }

  function fmtNum(v, decimals) {
    if (v === null || v === undefined) return "—";
    var n = Number(v);
    if (Number.isNaN(n)) return "—";
    return n.toFixed(decimals);
  }

  function lastEmaValue(snapshot) {
    var co = snapshot && snapshot.chart_overlays;
    var lines = co && co.lines;
    var pts = lines && lines.ema_20;
    if (!pts || !pts.length) return null;
    var last = pts[pts.length - 1];
    var v = last && last.value;
    var n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function rsiPlain(rsi) {
    if (rsi === null || rsi === undefined || Number.isNaN(Number(rsi))) {
      return "Not enough data to describe RSI yet.";
    }
    var r = Number(rsi);
    if (r >= 70) return "RSI is high — the market has moved up quickly; some traders watch for a cool-off.";
    if (r <= 30) return "RSI is low — the market has sold off quickly; some traders watch for a bounce.";
    if (r >= 55) return "RSI is tilted upward — buyers have been a bit stronger lately.";
    if (r <= 45) return "RSI is tilted downward — sellers have been a bit stronger lately.";
    return "RSI is in a middle zone — no extreme stretch up or down.";
  }

  function macdPlain(macd) {
    var line = macd && macd.line;
    var sig = macd && macd.signal;
    var hist = macd && macd.histogram;
    if (
      line === null ||
      line === undefined ||
      sig === null ||
      sig === undefined ||
      hist === null ||
      hist === undefined ||
      [line, sig, hist].some(function (x) {
        return Number.isNaN(Number(x));
      })
    ) {
      return "MACD parts are not all available on this bar yet.";
    }
    var h = Number(hist);
    var l = Number(line);
    var s = Number(sig);
    if (h > 0 && l > s) return "Momentum is leaning upward — the fast line is above the slow line and the histogram is positive.";
    if (h < 0 && l < s) return "Momentum is leaning downward — the fast line is below the slow line and the histogram is negative.";
    if (h > 0) return "Momentum is nudging upward — the histogram is positive.";
    if (h < 0) return "Momentum is nudging downward — the histogram is negative.";
    return "Momentum is fairly balanced — MACD is not showing a strong push either way.";
  }

  function bollingerPlain(bb, lastClose) {
    var up = bb && bb.upper;
    var mid = bb && bb.middle;
    var lo = bb && bb.lower;
    var c = lastClose != null ? Number(lastClose) : NaN;
    if (
      up === null ||
      up === undefined ||
      mid === null ||
      mid === undefined ||
      lo === null ||
      lo === undefined ||
      [up, mid, lo].some(function (x) {
        return Number.isNaN(Number(x));
      })
    ) {
      return "Band levels are not ready yet.";
    }
    if (!Number.isFinite(c)) {
      return "Bands show typical high/low envelopes around the middle line; when price hugs a band, the move has been sharp.";
    }
    var u = Number(up);
    var m = Number(mid);
    var l = Number(lo);
    var span = u - l;
    if (span <= 0) return "Price is inside the volatility bands.";
    var tu = u - span * 0.08;
    var tl = l + span * 0.08;
    if (c >= tu) return "Price is near the upper band — the recent move has been strong to the upside.";
    if (c <= tl) return "Price is near the lower band — the recent move has been strong to the downside.";
    return "Price is between the bands — a calmer stretch relative to recent volatility.";
  }

  function trendSummary(snapshot) {
    var ema = lastEmaValue(snapshot);
    var close = snapshot && snapshot.last_close;
    var c = close != null ? Number(close) : NaN;
    if (ema === null || !Number.isFinite(c)) {
      return "Once the trend line has enough bars, we will compare the last close to that line in plain language.";
    }
    var diff = ((c - ema) / ema) * 100;
    var eps = 0.03;
    if (diff > eps) {
      return (
        "Last close is above the trend line — short-term price is stronger than the smoothed average (" +
        fmtNum(c, 2) +
        " vs EMA " +
        fmtNum(ema, 2) +
        ")."
      );
    }
    if (diff < -eps) {
      return (
        "Last close is below the trend line — short-term price is weaker than the smoothed average (" +
        fmtNum(c, 2) +
        " vs EMA " +
        fmtNum(ema, 2) +
        ")."
      );
    }
    return (
      "Last close is about equal to the trend line (" +
      fmtNum(c, 2) +
      " vs EMA " +
      fmtNum(ema, 2) +
      ") — price is hugging the average."
    );
  }

  function render(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return;

    var warm = el("ind-warmup");
    var bars = el("ind-bars");
    var need = el("ind-need");
    var asof = el("ind-asof");
    var trendEl = el("ind-trend-summary");
    var rsiPlainEl = el("ind-rsi-plain");
    var macdPlainEl = el("ind-macd-plain");
    var bbPlainEl = el("ind-bb-plain");

    if (warm) warm.hidden = !!snapshot.sufficient_data;
    if (bars) bars.textContent = snapshot.bars_used != null ? String(snapshot.bars_used) : "—";
    if (need) need.textContent = snapshot.minimum_bars_required != null ? String(snapshot.minimum_bars_required) : "—";
    if (asof) {
      var t = snapshot.as_of_candle_time;
      asof.textContent = t != null ? String(t) : "—";
    }

    if (trendEl) trendEl.textContent = trendSummary(snapshot);

    var emaVal = lastEmaValue(snapshot);
    var lc = snapshot && snapshot.last_close;
    var closeNum = lc != null ? Number(lc) : NaN;
    var closeDisp = el("ind-trend-close-display");
    var emaDisp = el("ind-trend-ema-display");
    if (closeDisp) closeDisp.textContent = Number.isFinite(closeNum) ? fmtNum(closeNum, 2) : "—";
    if (emaDisp) emaDisp.textContent = emaVal !== null ? fmtNum(emaVal, 2) : "—";

    var rsi = el("ind-rsi");
    if (rsi) rsi.textContent = fmtNum(snapshot.rsi_14, 2);
    if (rsiPlainEl) rsiPlainEl.textContent = rsiPlain(snapshot.rsi_14);

    var macd = snapshot.macd || {};
    var mLine = el("ind-macd-line");
    var mSig = el("ind-macd-signal");
    var mHist = el("ind-macd-hist");
    if (mLine) mLine.textContent = fmtNum(macd.line, 6);
    if (mSig) mSig.textContent = fmtNum(macd.signal, 6);
    if (mHist) mHist.textContent = fmtNum(macd.histogram, 6);
    if (macdPlainEl) macdPlainEl.textContent = macdPlain(macd);

    var bb = snapshot.bollinger || {};
    var bbU = el("ind-bb-upper");
    var bbM = el("ind-bb-middle");
    var bbL = el("ind-bb-lower");
    if (bbU) bbU.textContent = fmtNum(bb.upper, 2);
    if (bbM) bbM.textContent = fmtNum(bb.middle, 2);
    if (bbL) bbL.textContent = fmtNum(bb.lower, 2);
    if (bbPlainEl) bbPlainEl.textContent = bollingerPlain(bb, snapshot.last_close);

    if (global.RagxStrategyPanel && typeof global.RagxStrategyPanel.refreshDecisionMeta === "function") {
      global.RagxStrategyPanel.refreshDecisionMeta();
    }
  }

  global.RagxIndicatorsPanel = { render: render };
})(typeof window !== "undefined" ? window : globalThis);
