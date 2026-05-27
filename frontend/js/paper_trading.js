/**
 * Local paper trading — simulates entries from final BUY/SELL + suggested_trade_plan.
 * Stored in localStorage only; no broker, no real orders.
 */

(function (global) {
  "use strict";

  var STORAGE_KEY = "ragx_paper_trading_v1";
  var MAX_CLOSED = 200;
  var BE_EPS_FRAC = 0.00008; // ~0.008% of price → breakeven

  var enabled = false;
  var trades = [];
  var lastInd = null;
  var lastStrategy = null;

  function el(id) {
    return document.getElementById(id);
  }

  function loadState() {
    try {
      var raw = global.localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      var j = JSON.parse(raw);
      if (j && typeof j === "object") {
        if (typeof j.enabled === "boolean") enabled = j.enabled;
        if (Array.isArray(j.trades)) trades = j.trades;
      }
    } catch {
      trades = [];
    }
  }

  function saveState() {
    try {
      global.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          v: 1,
          enabled: enabled,
          trades: trades,
        })
      );
    } catch {
      /* quota / private mode */
    }
  }

  function newInternalId() {
    return "pt_" + Date.now().toString(36) + "_" + Math.random().toString(36).slice(2, 10);
  }

  function num(x) {
    var n = Number(x);
    return Number.isFinite(n) ? n : NaN;
  }

  function findMarkerSignalId(markers, asOf, side) {
    if (!markers || !markers.length) return null;
    var i;
    var as = Math.floor(Number(asOf));
    if (!Number.isFinite(as)) return null;
    for (i = markers.length - 1; i >= 0; i--) {
      var m = markers[i];
      if (String(m.strategy_source || "") !== "combined_signal") continue;
      if (String(m.action || "").toLowerCase() !== side) continue;
      if (Math.floor(Number(m.timestamp)) !== as) continue;
      var sid = m.signal_id || m.id;
      if (sid) return String(sid);
    }
    return null;
  }

  function signalIdExists(sid) {
    var i;
    for (i = 0; i < trades.length; i++) {
      if (trades[i].signal_id === sid) return true;
    }
    return false;
  }

  function tryOpenFromSnapshots() {
    if (!enabled || !lastInd || !lastStrategy) return;

    var snap = lastStrategy;
    var final = snap.final || {};
    var sig = String(final.signal || "").toLowerCase();
    if (sig !== "buy" && sig !== "sell") return;

    var plan = snap.suggested_trade_plan;
    if (!plan || String(plan.side || "").toLowerCase() !== sig) return;

    var asOf = lastInd.as_of_candle_time;
    if (asOf == null) return;
    var as = Math.floor(Number(asOf));
    if (!Number.isFinite(as) || as <= 0) return;

    var tf =
      global.RagxTimeframe && typeof global.RagxTimeframe.getCurrent === "function"
        ? global.RagxTimeframe.getCurrent()
        : "1m";

    var sid = findMarkerSignalId(snap.signal_markers || [], as, sig);
    if (!sid) sid = "paper:" + tf + ":" + as + ":" + sig;

    if (signalIdExists(sid)) return;

    var entryPx = num(plan.entry);
    var stopPx = num(plan.stop_loss);
    var tpPx = num(plan.take_profit);
    if (!Number.isFinite(entryPx) || !Number.isFinite(stopPx) || !Number.isFinite(tpPx)) return;

    trades.push({
      id: newInternalId(),
      signal_id: sid,
      timeframe: tf,
      side: sig,
      entry_time_unix: as,
      entry_price: entryPx,
      stop_loss: stopPx,
      take_profit: tpPx,
      status: "open",
      exit_time_unix: null,
      exit_price: null,
      outcome: null,
      pnl: null,
    });
    saveState();
    render();
  }

  /**
   * Conservative same-bar rule: if stop and target both trade in one bar, assume stop hit first.
   */
  function exitDecisionForLong(o, h, l, stop, tp) {
    var hitStop = l <= stop;
    var hitTp = h >= tp;
    if (hitStop && hitTp) return { price: stop, reason: "stop" };
    if (hitStop) return { price: stop, reason: "stop" };
    if (hitTp) return { price: tp, reason: "target" };
    return null;
  }

  function exitDecisionForShort(o, h, l, stop, tp) {
    var hitStop = h >= stop;
    var hitTp = l <= tp;
    if (hitStop && hitTp) return { price: stop, reason: "stop" };
    if (hitStop) return { price: stop, reason: "stop" };
    if (hitTp) return { price: tp, reason: "target" };
    return null;
  }

  function closeTrade(tr, exitTimeUnix, exitPrice, reason) {
    tr.status = "closed";
    tr.exit_time_unix = Math.floor(Number(exitTimeUnix));
    tr.exit_price = exitPrice;

    var entry = num(tr.entry_price);
    var x = num(exitPrice);
    var pnl;
    if (tr.side === "buy") pnl = x - entry;
    else pnl = entry - x;

    tr.pnl = Math.round(pnl * 1e6) / 1e6;

    var be = Math.abs(pnl) < Math.abs(entry) * BE_EPS_FRAC;
    if (be) {
      tr.outcome = "breakeven";
      tr.pnl = 0;
    } else if (pnl > 0) tr.outcome = "win";
    else if (pnl < 0) tr.outcome = "loss";
    else tr.outcome = "breakeven";

    trimClosedTrades();
    saveState();
    render();
  }

  function trimClosedTrades() {
    var closed = trades.filter(function (t) {
      return t.status === "closed";
    });
    if (closed.length <= MAX_CLOSED) return;
    closed.sort(function (a, b) {
      return (Number(a.exit_time_unix) || 0) - (Number(b.exit_time_unix) || 0);
    });
    var drop = closed.slice(0, closed.length - MAX_CLOSED);
    var dropIds = {};
    var k;
    for (k = 0; k < drop.length; k++) dropIds[drop[k].id] = true;
    trades = trades.filter(function (t) {
      return !dropIds[t.id];
    });
  }

  function processCandle(c) {
    var hasOpen = false;
    var q;
    for (q = 0; q < trades.length; q++) {
      if (trades[q].status === "open") {
        hasOpen = true;
        break;
      }
    }
    if (!hasOpen) return;

    var t = Math.floor(Number(c.time));
    var o = num(c.open);
    var h = num(c.high);
    var l = num(c.low);
    var cl = num(c.close);
    if (!Number.isFinite(t) || !Number.isFinite(h) || !Number.isFinite(l)) return;

    var i;
    var toClose = [];
    for (i = 0; i < trades.length; i++) {
      var tr = trades[i];
      if (tr.status !== "open") continue;

      var dec =
        tr.side === "buy"
          ? exitDecisionForLong(o, h, l, tr.stop_loss, tr.take_profit)
          : exitDecisionForShort(o, h, l, tr.stop_loss, tr.take_profit);

      if (dec) {
        toClose.push({ tr: tr, price: dec.price, reason: dec.reason, barTime: t });
      }
    }

    for (i = 0; i < toClose.length; i++) {
      var item = toClose[i];
      closeTrade(item.tr, item.barTime, item.price, item.reason);
    }
  }

  function formatTs(unix) {
    var ts = Math.floor(Number(unix));
    if (!Number.isFinite(ts) || ts <= 0) return "—";
    if (global.RagxChart && typeof global.RagxChart.formatCentralAxisTime === "function") {
      return global.RagxChart.formatCentralAxisTime(ts);
    }
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Chicago",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      }).format(new Date(ts * 1000));
    } catch {
      return String(ts);
    }
  }

  function fmtPx(p) {
    var x = num(p);
    if (!Number.isFinite(x)) return "—";
    if (Math.abs(x) >= 1000) return x.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return x.toFixed(4);
  }

  function fmtPnl(p) {
    var x = num(p);
    if (!Number.isFinite(x)) return "—";
    var s = (x >= 0 ? "+" : "") + x.toLocaleString(undefined, { maximumFractionDigits: 2 });
    return s + " USDT";
  }

  function outcomeClass(o) {
    if (o === "win") return "paper-outcome-win";
    if (o === "loss") return "paper-outcome-loss";
    return "paper-outcome-be";
  }

  function renderList(container, list, emptyMsg, isOpen) {
    if (!container) return;
    container.innerHTML = "";
    if (!list.length) {
      var p = global.document.createElement("p");
      p.className = "paper-list-empty";
      p.textContent = emptyMsg;
      container.appendChild(p);
      return;
    }
    var i;
    for (i = 0; i < list.length; i++) {
      var tr = list[i];
      var card = global.document.createElement("div");
      card.className = "paper-trade-card";

      var top = global.document.createElement("div");
      top.className = "paper-trade-top";
      var sideEl = global.document.createElement("span");
      sideEl.className = "paper-trade-side paper-trade-side-" + tr.side;
      sideEl.textContent = tr.side.toUpperCase();
      var tfEl = global.document.createElement("span");
      tfEl.className = "paper-trade-tf";
      tfEl.textContent = tr.timeframe || "—";
      top.appendChild(sideEl);
      top.appendChild(tfEl);

      var grid = global.document.createElement("dl");
      grid.className = "paper-trade-dl";

      function addRow(label, val) {
        var dt = global.document.createElement("dt");
        dt.textContent = label;
        var dd = global.document.createElement("dd");
        dd.textContent = val;
        grid.appendChild(dt);
        grid.appendChild(dd);
      }

      addRow("Entry", formatTs(tr.entry_time_unix) + " · " + fmtPx(tr.entry_price));
      addRow("Stop / Target", fmtPx(tr.stop_loss) + " / " + fmtPx(tr.take_profit));
      addRow("Signal id", tr.signal_id.length > 36 ? tr.signal_id.slice(0, 34) + "…" : tr.signal_id);

      if (!isOpen) {
        addRow("Exit", formatTs(tr.exit_time_unix) + " · " + fmtPx(tr.exit_price));
        var oc = global.document.createElement("dd");
        oc.className = "paper-trade-outcome " + outcomeClass(tr.outcome);
        oc.textContent = String(tr.outcome || "—").toUpperCase();
        var odt = global.document.createElement("dt");
        odt.textContent = "Outcome";
        grid.appendChild(odt);
        grid.appendChild(oc);
        addRow("PnL (1 unit)", fmtPnl(tr.pnl));
      }

      card.appendChild(top);
      card.appendChild(grid);
      container.appendChild(card);
    }
  }

  function render() {
    var toggle = el("paper-trade-toggle");
    if (toggle) toggle.checked = enabled;

    var banner = el("paper-mode-banner");
    if (banner) banner.hidden = enabled;

    var openList = trades.filter(function (t) {
      return t.status === "open";
    });
    var closedList = trades
      .filter(function (t) {
        return t.status === "closed";
      })
      .sort(function (a, b) {
        return (Number(b.exit_time_unix) || 0) - (Number(a.exit_time_unix) || 0);
      });

    renderList(el("paper-open-list"), openList, "No open simulated positions.", true);
    renderList(el("paper-closed-list"), closedList.slice(0, 50), "No closed trades yet.", false);

    var sumEl = el("paper-stats-summary");
    if (sumEl) {
      var closed = trades.filter(function (t) {
        return t.status === "closed";
      });
      var wins = closed.filter(function (t) {
        return t.outcome === "win";
      }).length;
      var losses = closed.filter(function (t) {
        return t.outcome === "loss";
      }).length;
      var totalPnl = 0;
      var j;
      for (j = 0; j < closed.length; j++) {
        totalPnl += num(closed[j].pnl) || 0;
      }
      var symHint =
        global.RagxMarket && typeof global.RagxMarket.getSymbol === "function"
          ? global.RagxMarket.getSymbol()
          : "BTCUSDT";
      sumEl.textContent =
        closed.length +
        " closed · " +
        wins +
        " W / " +
        losses +
        " L · Σ PnL " +
        fmtPnl(totalPnl) +
        " (per 1 unit · " +
        symHint +
        ", excl. fees)";
    }
  }

  function setEnabled(on) {
    enabled = !!on;
    saveState();
    render();
  }

  function clearAllTrades() {
    trades = [];
    saveState();
    render();
  }

  function init() {
    loadState();
    var toggle = el("paper-trade-toggle");
    if (toggle) {
      toggle.checked = enabled;
      toggle.addEventListener("change", function () {
        setEnabled(toggle.checked);
      });
    }
    var clr = el("paper-clear-btn");
    if (clr) {
      clr.addEventListener("click", function () {
        if (global.confirm("Clear all paper trades from this browser?")) clearAllTrades();
      });
    }
    render();
  }

  function cacheIndicators(snap) {
    if (!snap || typeof snap !== "object") return;
    lastInd = snap;
    tryOpenFromSnapshots();
  }

  function onStrategy(snap) {
    if (!snap || typeof snap !== "object") return;
    lastStrategy = snap;
    tryOpenFromSnapshots();
  }

  function onCandle(c) {
    processCandle(c);
  }

  global.RagxPaperTrading = {
    init: init,
    setEnabled: setEnabled,
    isEnabled: function () {
      return enabled;
    },
    cacheIndicators: cacheIndicators,
    onStrategy: onStrategy,
    onCandle: onCandle,
    render: render,
  };
})(typeof window !== "undefined" ? window : globalThis);
