/**
 * Local backtest dashboard — GET /api/backtest and /api/backtest/compare.
 */

(function (global) {
  "use strict";

  function el(id) {
    return document.getElementById(id);
  }

  function setText(id, text) {
    var n = el(id);
    if (n) n.textContent = text;
  }

  function fmt(v) {
    if (v === null || v === undefined) return "—";
    if (typeof v === "number" && Number.isFinite(v)) return String(v);
    return String(v);
  }

  function renderKpis(m) {
    if (!m) m = {};
    setText("bt-kpi-trades", fmt(m.total_trades));
    setText(
      "bt-kpi-winrate",
      m.win_rate != null ? (m.win_rate * 100).toFixed(1) + "%" : "—"
    );
    var pf = el("bt-kpi-pf");
    if (pf) {
      if (m.profit_factor_is_infinite) pf.textContent = "∞";
      else pf.textContent = fmt(m.profit_factor);
    }
    setText("bt-kpi-dd", fmt(m.max_drawdown));
    setText("bt-kpi-ret", fmt(m.total_return));
    setText("bt-kpi-open", fmt(m.open_at_end));
  }

  function renderAdvanced(m) {
    if (!m) m = {};
    setText(
      "bt-m-lossrate",
      m.loss_rate != null ? (m.loss_rate * 100).toFixed(1) + "%" : "—"
    );
    setText(
      "bt-m-wl",
      fmt(m.wins) + " / " + fmt(m.losses) + " / " + fmt(m.breakevens)
    );
    setText("bt-m-avgwin", fmt(m.average_win));
    setText("bt-m-avgloss", fmt(m.average_loss));
    setText(
      "bt-m-conf",
      fmt(m.average_confidence_win) +
        " / " +
        fmt(m.average_confidence_loss) +
        " / " +
        fmt(m.average_confidence_breakeven)
    );
  }

  function renderEquity(curve) {
    var svg = el("bt-equity-svg");
    if (!svg) return;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    var ns = "http://www.w3.org/2000/svg";
    if (!curve || !curve.length) {
      var t = global.document.createElementNS(ns, "text");
      t.setAttribute("x", "140");
      t.setAttribute("y", "30");
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("class", "bt-equity-empty");
      t.setAttribute("fill", "currentColor");
      t.setAttribute("font-size", "11");
      t.textContent = "No closed trades";
      svg.appendChild(t);
      return;
    }

    var vals = curve.map(function (p) {
      return p.equity;
    });
    var minV = Math.min.apply(null, vals);
    var maxV = Math.max.apply(null, vals);
    var span = maxV - minV;
    var pad = span > 0 ? span * 0.08 : Math.max(Math.abs(maxV), 1e-6) * 0.08;
    var lo = minV - pad;
    var hi = maxV + pad;
    if (lo === hi) {
      lo -= 1;
      hi += 1;
    }

    var W = 280;
    var H = 56;
    var margin = { l: 4, r: 4, t: 6, b: 10 };
    var iw = W - margin.l - margin.r;
    var ih = H - margin.t - margin.b;
    var n = curve.length;
    var pts = [];
    var i;
    for (i = 0; i < n; i++) {
      var x = margin.l + (iw * i) / Math.max(n - 1, 1);
      var eq = curve[i].equity;
      var yn = (eq - lo) / (hi - lo);
      var y = margin.t + ih * (1 - yn);
      pts.push(x.toFixed(2) + "," + y.toFixed(2));
    }

    var rect = global.document.createElementNS(ns, "rect");
    rect.setAttribute("x", "0");
    rect.setAttribute("y", "0");
    rect.setAttribute("width", String(W));
    rect.setAttribute("height", String(H));
    rect.setAttribute("class", "bt-equity-bg");
    svg.appendChild(rect);

    var zeroY = margin.t + ih * (1 - (0 - lo) / (hi - lo));
    if (zeroY >= margin.t && zeroY <= margin.t + ih) {
      var zl = global.document.createElementNS(ns, "line");
      zl.setAttribute("x1", String(margin.l));
      zl.setAttribute("x2", String(W - margin.r));
      zl.setAttribute("y1", zeroY.toFixed(2));
      zl.setAttribute("y2", zeroY.toFixed(2));
      zl.setAttribute("class", "bt-equity-zero");
      svg.appendChild(zl);
    }

    var pl = global.document.createElementNS(ns, "polyline");
    pl.setAttribute("fill", "none");
    pl.setAttribute("stroke", "currentColor");
    pl.setAttribute("stroke-width", "1.25");
    pl.setAttribute("stroke-linejoin", "round");
    pl.setAttribute("stroke-linecap", "round");
    pl.setAttribute("class", "bt-equity-line");
    pl.setAttribute("points", pts.join(" "));
    svg.appendChild(pl);
  }

  function renderStrategyRows(rows) {
    var tbody = el("bt-strat-body");
    if (!tbody) return;
    tbody.innerHTML = "";
    if (!rows || !rows.length) {
      var tr0 = global.document.createElement("tr");
      var td = global.document.createElement("td");
      td.colSpan = 3;
      td.className = "bt-strat-empty";
      td.textContent = "No aligned leg data for this run.";
      tr0.appendChild(td);
      tbody.appendChild(tr0);
      return;
    }
    var j;
    for (j = 0; j < rows.length; j++) {
      var r = rows[j];
      var tr = global.document.createElement("tr");
      var name = r.strategy_name || r.strategy_id || "—";
      var nAl = r.aligned_trades != null ? String(r.aligned_trades) : "—";
      var wr =
        r.win_rate_when_aligned != null
          ? (r.win_rate_when_aligned * 100).toFixed(1) + "%"
          : "—";
      tr.innerHTML =
        "<td>" +
        name +
        "</td><td>" +
        nAl +
        "</td><td>" +
        wr +
        "</td>";
      tbody.appendChild(tr);
    }
  }

  function renderCompareTable(data) {
    var wrap = el("bt-compare-wrap");
    var tbody = el("bt-compare-body");
    var banner = el("bt-best-banner");
    var bestIv = el("bt-best-interval");
    var bestMeta = el("bt-best-meta");
    if (!wrap || !tbody) return;

    var rows = (data && data.by_interval) || [];
    if (!rows.length) {
      wrap.hidden = true;
      if (banner) banner.hidden = true;
      return;
    }
    wrap.hidden = false;
    tbody.innerHTML = "";
    var k;
    for (k = 0; k < rows.length; k++) {
      var row = rows[k];
      var m = row.metrics || {};
      var tr = global.document.createElement("tr");
      if (data.best_interval && row.interval === data.best_interval) {
        tr.className = "bt-compare-best";
      }
      var pf = m.profit_factor_is_infinite ? "∞" : fmt(m.profit_factor);
      tr.innerHTML =
        "<td>" +
        fmt(row.interval) +
        "</td><td>" +
        fmt(m.total_trades) +
        "</td><td>" +
        (m.win_rate != null ? (m.win_rate * 100).toFixed(0) + "%" : "—") +
        "</td><td>" +
        pf +
        "</td><td>" +
        fmt(m.max_drawdown) +
        "</td><td>" +
        fmt(m.total_return) +
        "</td>";
      tbody.appendChild(tr);
    }

    if (banner && bestIv) {
      if (data.best_interval) {
        banner.hidden = false;
        bestIv.textContent = data.best_interval;
        if (bestMeta) {
          var br = data.best_total_return;
          bestMeta.textContent =
            br != null && typeof br === "number"
              ? " · return " + br
              : "";
        }
      } else {
        banner.hidden = true;
      }
    }
  }

  function renderTrades(inc, data) {
    var wrap = el("bt-trades-wrap");
    if (wrap) wrap.hidden = !inc;
    var tbody = el("bt-trades-body");
    if (!tbody) return;
    tbody.innerHTML = "";
    if (!inc) return;
    var rows = data.closed_trades || [];
    var i;
    for (i = 0; i < rows.length; i++) {
      var t = rows[i];
      var sid = String(t.signal_id || "");
      var sidShow = sid.length > 24 ? sid.slice(0, 22) + "…" : sid;
      var tr = global.document.createElement("tr");
      tr.innerHTML =
        "<td>" +
        String(t.side || "").toUpperCase() +
        "</td><td>" +
        fmt(t.outcome) +
        "</td><td>" +
        fmt(t.pnl) +
        "</td><td>" +
        fmt(t.confidence) +
        "</td><td>" +
        sidShow +
        "</td>";
      tbody.appendChild(tr);
    }
    if (!rows.length) {
      var tr0 = global.document.createElement("tr");
      var td = global.document.createElement("td");
      td.colSpan = 5;
      td.className = "bt-trades-empty";
      td.textContent = "No closed trades in this window.";
      tr0.appendChild(td);
      tbody.appendChild(tr0);
    }
  }

  function runBacktest() {
    var ivEl = el("bt-interval");
    var limEl = el("bt-limit");
    var incEl = el("bt-include-trades");
    var status = el("bt-status");
    var iv = ivEl ? ivEl.value : "5m";
    var lim = limEl ? Math.max(80, Math.min(1000, parseInt(limEl.value, 10) || 600)) : 600;
    var inc = incEl && incEl.checked;

    if (status) {
      status.hidden = false;
      status.textContent = "Running backtest…";
    }
    var q = new URLSearchParams();
    q.set("interval", iv);
    q.set("limit", String(lim));
    q.set("include_trades", inc ? "true" : "false");

    global
      .fetch("/api/backtest?" + q.toString(), { cache: "no-store" })
      .then(function (r) {
        if (!r.ok)
          return r.text().then(function (t) {
            throw new Error(t || String(r.status));
          });
        return r.json();
      })
      .then(function (data) {
        if (status)
          status.textContent =
            data.symbol +
            " · " +
            data.interval +
            " · " +
            (data.bars_used || 0) +
            " bars";
        var m = data.metrics || {};
        renderKpis(m);
        renderAdvanced(m);
        renderEquity(data.equity_curve || []);
        renderStrategyRows(data.strategy_contributions || []);
        var note = el("bt-note");
        if (note)
          note.textContent =
            data.note || (data.closed_trades_truncated ? "Trade list truncated." : "");
        renderTrades(inc, data);
      })
      .catch(function (err) {
        if (status)
          status.textContent =
            "Backtest failed: " + (err && err.message ? err.message : String(err));
        renderKpis({});
        renderAdvanced({});
        renderEquity([]);
        renderStrategyRows([]);
        renderTrades(false, {});
      });
  }

  function runCompare() {
    var limEl = el("bt-limit");
    var status = el("bt-status");
    var lim = limEl ? Math.max(80, Math.min(1000, parseInt(limEl.value, 10) || 600)) : 600;

    if (status) {
      status.hidden = false;
      status.textContent = "Comparing all timeframes…";
    }
    var q = new URLSearchParams();
    q.set("limit", String(lim));

    global
      .fetch("/api/backtest/compare?" + q.toString(), { cache: "no-store" })
      .then(function (r) {
        if (!r.ok)
          return r.text().then(function (t) {
            throw new Error(t || String(r.status));
          });
        return r.json();
      })
      .then(function (data) {
        if (status)
          status.textContent =
            "Compared " +
            (data.symbol || "") +
            " · " +
            (data.limit || lim) +
            " bars each";
        renderCompareTable(data);
      })
      .catch(function (err) {
        if (status)
          status.textContent =
            "Compare failed: " + (err && err.message ? err.message : String(err));
      });
  }

  function mount() {
    var btn = el("bt-run-btn");
    if (btn) btn.addEventListener("click", runBacktest);
    var cbtn = el("bt-compare-btn");
    if (cbtn) cbtn.addEventListener("click", runCompare);
  }

  global.RagxBacktestPanel = {
    mount: mount,
    runBacktest: runBacktest,
    runCompare: runCompare,
  };
})(typeof window !== "undefined" ? window : globalThis);
