/**
 * Final combined markers on candles: BUY, SELL, EXIT (Lightweight Charts v4).
 *
 * Only `strategy_source === "combined_signal"`. Sub-strategies never draw here.
 * Server suppresses noisy same-direction repeats; client dedupes again before setMarkers.
 */

(function (global) {
  "use strict";

  var COMBINED = "combined_signal";
  var TZ = "America/Chicago";
  var CLIENT_CONF_JUMP = 6;

  function timeToUnix(t) {
    if (typeof t === "number" && Number.isFinite(t)) return Math.floor(t);
    if (t && typeof t === "object" && "year" in t && "month" in t && "day" in t) {
      return Math.floor(Date.UTC(t.year, t.month - 1, t.day) / 1000);
    }
    return null;
  }

  function intTs(x) {
    var n = Math.floor(Number(x));
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  function formatTooltipTime(unixSec) {
    var ts = intTs(unixSec);
    if (ts === null) return "—";
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: TZ,
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
        timeZoneName: "short",
      }).format(new Date(ts * 1000));
    } catch {
      return String(ts);
    }
  }

  function markerColor(action) {
    if (action === "buy") return "#3fb950";
    if (action === "sell") return "#f85149";
    return "#d4a72c";
  }

  function markerColorHighlight(action) {
    if (action === "buy") return "#56f070";
    if (action === "sell") return "#ff7a72";
    return "#f0c14a";
  }

  function markerShape(action) {
    if (action === "buy") return "arrowUp";
    if (action === "sell") return "arrowDown";
    return "circle";
  }

  function markerPosition(action) {
    if (action === "buy") return "belowBar";
    if (action === "sell") return "aboveBar";
    return "inBar";
  }

  function isFinalChartMarker(m) {
    if (!m || String(m.strategy_source || "") !== COMBINED) return false;
    var act = String(m.action || "").toLowerCase();
    return act === "buy" || act === "sell" || act === "exit";
  }

  /**
   * Drop redundant same-direction markers in a short window unless confidence rose (mirrors server).
   */
  function dedupeFinalMarkers(markers, barSec) {
    var combined = [];
    var i;
    for (i = 0; i < markers.length; i++) {
      if (isFinalChartMarker(markers[i])) combined.push(markers[i]);
    }
    combined.sort(function (a, b) {
      return (intTs(a.timestamp) || 0) - (intTs(b.timestamp) || 0);
    });
    var windowSec = Math.max((barSec || 60) * 3, 120);
    var out = [];
    var lastDir = null;
    for (i = 0; i < combined.length; i++) {
      var m = combined[i];
      var act = String(m.action || "").toLowerCase();
      var t = intTs(m.timestamp);
      if (t === null) continue;
      if (act === "exit") {
        out.push(m);
        lastDir = null;
        continue;
      }
      if (act === "buy" || act === "sell") {
        var c = Number(m.confidence) || 0;
        if (
          lastDir &&
          lastDir.act === act &&
          t - lastDir.t < windowSec &&
          c < lastDir.c + CLIENT_CONF_JUMP
        ) {
          continue;
        }
        lastDir = { act: act, t: t, c: c };
        out.push(m);
      }
    }
    return out;
  }

  /**
   * @param {object} chart
   * @param {object} candleSeries
   * @param {HTMLElement} containerEl — position:relative; holds tooltip
   * @param {object} [callbacks]
   */
  function createLayer(chart, candleSeries, containerEl, callbacks) {
    callbacks = callbacks || {};
    var dashboardSymbol = "";
    var currentMarkers = [];
    var pinned = null;
    var lastPayload = null;
    var highlightSignalIdSet = new Set();

    var tooltip = global.document.createElement("div");
    tooltip.className = "signal-marker-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.hidden = true;
    if (containerEl) containerEl.appendChild(tooltip);

    function hideTip() {
      tooltip.hidden = true;
      while (tooltip.firstChild) tooltip.removeChild(tooltip.firstChild);
    }

    if (containerEl) {
      containerEl.addEventListener("mouseleave", function () {
        if (!pinned) hideTip();
      });
    }

    function positionTip(px, py) {
      if (!containerEl) return;
      var pad = 10;
      var tw = 280;
      var x = (px || 0) + pad;
      var y = (py || 0) + pad;
      var cr = containerEl.getBoundingClientRect();
      if (x + tw > cr.width - 4) x = Math.max(pad, cr.width - tw - 4);
      tooltip.style.left = x + "px";
      tooltip.style.top = y + "px";
    }

    function signalTypeLabel(act) {
      if (act === "exit") return "EXIT";
      if (act === "buy") return "BUY";
      if (act === "sell") return "SELL";
      return String(act || "—").toUpperCase();
    }

    function fillTooltip(markers) {
      hideTip();
      var i;
      for (i = 0; i < markers.length; i++) {
        var m = markers[i];
        var act = String(m.action || "").toLowerCase();
        var block = global.document.createElement("div");
        block.className = "smt-block";

        var typeLine = global.document.createElement("div");
        var typeMod =
          act === "exit" ? "exit" : act === "sell" ? "sell" : act === "buy" ? "buy" : "exit";
        typeLine.className = "smt-signal-type smt-signal-type-" + typeMod;
        typeLine.textContent = "Signal: " + signalTypeLabel(act);
        block.appendChild(typeLine);

        var sub = global.document.createElement("div");
        sub.className = "smt-sub";
        sub.textContent = "Final combined call (not individual rules)";
        block.appendChild(sub);

        var timeRow = global.document.createElement("div");
        timeRow.className = "smt-time";
        timeRow.textContent = "Time: " + formatTooltipTime(m.timestamp);
        block.appendChild(timeRow);

        var conf = global.document.createElement("div");
        conf.className = "smt-conf";
        conf.textContent = "Confidence: " + String(m.confidence != null ? m.confidence : "—") + " / 100";
        block.appendChild(conf);

        var expl = global.document.createElement("p");
        expl.className = "smt-expl";
        var explText = String(m.explanation_text || m.label || "").trim();
        expl.textContent = explText || "—";
        block.appendChild(expl);

        tooltip.appendChild(block);
      }
      tooltip.hidden = false;
    }

    function markersAtUnix(tu) {
      if (tu === null) return [];
      var out = [];
      var i;
      for (i = 0; i < currentMarkers.length; i++) {
        var m = currentMarkers[i];
        if (!isFinalChartMarker(m)) continue;
        if (intTs(m.timestamp) === tu) out.push(m);
      }
      return out;
    }

    function showAt(px, py, markers) {
      fillTooltip(markers);
      positionTip(px, py);
    }

    function highlightedUnixSet() {
      var out = new Set();
      var i;
      for (i = 0; i < currentMarkers.length; i++) {
        var m = currentMarkers[i];
        if (!isFinalChartMarker(m)) continue;
        var sid = String(m.signal_id || m.id || "");
        if (!sid || !highlightSignalIdSet.has(sid)) continue;
        var tu = intTs(m.timestamp);
        if (tu) out.add(tu);
      }
      return out;
    }

    function applyLcMarkers() {
      var tf =
        global.RagxTimeframe && typeof global.RagxTimeframe.getCurrent === "function"
          ? global.RagxTimeframe.getCurrent()
          : "1m";
      var barSec =
        global.RagxChartUpdater && typeof global.RagxChartUpdater.barStepSeconds === "function"
          ? global.RagxChartUpdater.barStepSeconds(tf)
          : 60;

      var hiTimes = highlightedUnixSet();
      var source = dedupeFinalMarkers(currentMarkers, barSec);
      var lc = [];
      var seen = {};
      var i;
      for (i = 0; i < source.length; i++) {
        var m = source[i];
        var ts = intTs(m.timestamp);
        var act = String(m.action || "").toLowerCase();
        if (!ts) continue;
        var key = ts + "_" + act;
        if (seen[key]) continue;
        seen[key] = true;
        var text = act === "buy" ? "BUY" : act === "sell" ? "SELL" : "EXIT";
        var hi = hiTimes.has(ts);
        var baseSize = act === "exit" ? 0.9 : 1;
        lc.push({
          time: ts,
          position: markerPosition(act),
          color: hi ? markerColorHighlight(act) : markerColor(act),
          shape: markerShape(act),
          text: text,
          size: hi ? baseSize * 1.45 : baseSize,
        });
      }
      try {
        candleSeries.setMarkers(lc);
      } catch {
        /* ignore */
      }
    }

    if (chart && typeof chart.subscribeCrosshairMove === "function") {
      chart.subscribeCrosshairMove(function (param) {
        if (pinned) return;
        if (!param || param.time === undefined || param.time === null) {
          hideTip();
          return;
        }
        var tu = timeToUnix(param.time);
        var hits = markersAtUnix(tu);
        if (!hits.length) {
          hideTip();
          return;
        }
        if (param.point) showAt(param.point.x, param.point.y, hits);
        else showAt(0, 0, hits);
      });
    }

    function signalIdsFromHits(hits) {
      var out = [];
      var i;
      for (i = 0; i < hits.length; i++) {
        var id = hits[i].signal_id || hits[i].id;
        if (id) out.push(String(id));
      }
      return out;
    }

    if (chart && typeof chart.subscribeClick === "function") {
      chart.subscribeClick(function (param) {
        if (!param || param.time === undefined || param.time === null) {
          pinned = null;
          hideTip();
          if (typeof callbacks.onMarkerDeselect === "function") callbacks.onMarkerDeselect();
          return;
        }
        var tu = timeToUnix(param.time);
        var hits = markersAtUnix(tu);
        if (hits.length) {
          pinned = { time: tu, x: param.point ? param.point.x : 0, y: param.point ? param.point.y : 0 };
          showAt(pinned.x, pinned.y, hits);
          if (typeof callbacks.onMarkerSelect === "function") {
            callbacks.onMarkerSelect({
              signalIds: signalIdsFromHits(hits),
              timestamp: tu,
              markers: hits,
            });
          }
        } else {
          pinned = null;
          hideTip();
          if (typeof callbacks.onMarkerDeselect === "function") callbacks.onMarkerDeselect();
        }
      });
    }

    return {
      setSymbol: function (sym) {
        dashboardSymbol = sym && String(sym).toUpperCase ? String(sym).toUpperCase() : String(sym || "");
      },

      clear: function () {
        currentMarkers = [];
        pinned = null;
        hideTip();
        try {
          candleSeries.setMarkers([]);
        } catch {
          /* ignore */
        }
      },

      apply: function (strategyPayload) {
        lastPayload = strategyPayload || null;
        var list = strategyPayload && strategyPayload.signal_markers;
        if (!Array.isArray(list)) {
          currentMarkers = [];
          applyLcMarkers();
          hideTip();
          return;
        }

        var tf =
          global.RagxTimeframe && typeof global.RagxTimeframe.getCurrent === "function"
            ? global.RagxTimeframe.getCurrent()
            : null;
        var first = list[0];
        if (first && first.timeframe && tf && first.timeframe !== tf) return;

        if (dashboardSymbol && list.length) {
          var sym0 = list[0].symbol;
          if (sym0 && String(sym0).toUpperCase() !== dashboardSymbol) return;
        }

        currentMarkers = list.slice();
        applyLcMarkers();
        pinned = null;
        hideTip();
      },

      getLastPayload: function () {
        return lastPayload;
      },

      setHighlightSignalIds: function (ids) {
        highlightSignalIdSet = new Set();
        if (ids && ids.length) {
          var j;
          for (j = 0; j < ids.length; j++) {
            if (ids[j] != null && String(ids[j]).length) highlightSignalIdSet.add(String(ids[j]));
          }
        }
        applyLcMarkers();
      },
    };
  }

  global.RagxSignalMarkers = { createLayer: createLayer, timeToUnix: timeToUnix };
})(typeof window !== "undefined" ? window : globalThis);
