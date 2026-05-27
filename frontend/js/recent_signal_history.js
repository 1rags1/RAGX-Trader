/**
 * Recent final signals from SQLite (/api/signal-history) — filters, debounced refresh, chart focus.
 */

(function (global) {
  "use strict";

  var DEBOUNCE_MS = 750;
  var FETCH_LIMIT = 200;
  var EXPL_LEN = 110;
  var debounceTimer = null;
  var abortCtrl = null;
  /** @type {Set<string>|null} null = show all actions */
  var filterActions = null;
  /** @type {string} empty = all timeframes */
  var filterTf = "";

  function el(id) {
    return document.getElementById(id);
  }

  function fmtPrice(n) {
    if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
    var x = Number(n);
    return x >= 1000 ? x.toLocaleString(undefined, { maximumFractionDigits: 2 }) : x.toFixed(2);
  }

  function shortExpl(text) {
    var s = String(text || "").replace(/\s+/g, " ").trim();
    if (s.length <= EXPL_LEN) return s;
    return s.slice(0, EXPL_LEN - 1) + "…";
  }

  function signalClass(sig) {
    var s = String(sig || "").toLowerCase();
    if (s === "buy") return "sig-buy";
    if (s === "sell") return "sig-sell";
    if (s === "exit") return "sig-exit";
    return "sig-neutral";
  }

  function formatBarTime(unixSec) {
    var ts = Math.floor(Number(unixSec));
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

  function buildQueryString() {
    var q = new URLSearchParams();
    q.set("limit", String(FETCH_LIMIT));
    if (filterActions && filterActions.size) {
      filterActions.forEach(function (a) {
        q.append("action", a);
      });
    }
    if (filterTf) q.set("timeframe", filterTf);
    return q.toString();
  }

  function syncActionChipUi() {
    var root = el("recent-filter-actions");
    if (!root) return;
    var buttons = root.querySelectorAll(".recent-chip[data-action-filter]");
    var i;
    for (i = 0; i < buttons.length; i++) {
      var b = buttons[i];
      var key = b.getAttribute("data-action-filter");
      if (key === "all") {
        b.classList.toggle("recent-chip--active", !filterActions || filterActions.size === 0);
        b.setAttribute("aria-pressed", !filterActions || filterActions.size === 0 ? "true" : "false");
        continue;
      }
      var on = filterActions && filterActions.has(key);
      b.classList.toggle("recent-chip--active", !!on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  function syncTfSelect() {
    var sel = el("recent-filter-tf");
    if (!sel) return;
    sel.value = filterTf || "";
  }

  function renderRows(rows) {
    var list = el("recent-signals-list");
    var empty = el("recent-signals-empty");
    var loading = el("recent-signals-loading");
    if (!list) return;
    if (loading) loading.hidden = true;

    var prevHi =
      global.RagxStrategyPanel && typeof global.RagxStrategyPanel.getHighlightedSignalIds === "function"
        ? global.RagxStrategyPanel.getHighlightedSignalIds()
        : [];

    list.innerHTML = "";
    if (!rows || !rows.length) {
      if (empty) {
        empty.hidden = false;
        empty.textContent = "No logged signals match these filters.";
      }
      return;
    }
    if (empty) empty.hidden = true;

    var j;
    for (j = 0; j < rows.length; j++) {
      var r = rows[j];
      var sid = r.signal_id;
      if (!sid) continue;
      var ct = r.candle_time_unix;
      var act = String(r.signal_type || "").toLowerCase();

      var btn = global.document.createElement("button");
      btn.type = "button";
      btn.className = "recent-sig-row recent-sig-row--history";
      btn.setAttribute("data-signal-id", String(sid));
      btn.setAttribute("data-timestamp", ct != null ? String(ct) : "0");
      btn.setAttribute("role", "listitem");
      btn.setAttribute(
        "aria-label",
        "Focus chart on " + act + " signal " + String(sid)
      );

      var top = global.document.createElement("div");
      top.className = "recent-sig-row-top";

      var timeEl = global.document.createElement("span");
      timeEl.className = "recent-sig-time";
      timeEl.textContent = formatBarTime(ct);

      var tfEl = global.document.createElement("span");
      tfEl.className = "recent-sig-tf";
      tfEl.textContent = r.timeframe || "—";

      var actEl = global.document.createElement("span");
      actEl.className = "recent-sig-action " + signalClass(act);
      actEl.textContent = act ? act.toUpperCase() : "—";

      var confEl = global.document.createElement("span");
      confEl.className = "recent-sig-conf";
      confEl.textContent = r.confidence != null ? String(r.confidence) + " / 100" : "—";

      top.appendChild(timeEl);
      top.appendChild(tfEl);
      top.appendChild(actEl);
      top.appendChild(confEl);

      var priceRow = global.document.createElement("div");
      priceRow.className = "recent-sig-price-row";
      var priceLabel = global.document.createElement("span");
      priceLabel.className = "recent-sig-price-label";
      priceLabel.textContent = "Price";
      var priceVal = global.document.createElement("span");
      priceVal.className = "recent-sig-price-val";
      priceVal.textContent = fmtPrice(r.price) + " USDT";
      priceRow.appendChild(priceLabel);
      priceRow.appendChild(priceVal);

      var expl = global.document.createElement("div");
      expl.className = "recent-sig-expl";
      expl.textContent = shortExpl(r.explanation);

      btn.appendChild(top);
      btn.appendChild(priceRow);
      btn.appendChild(expl);

      (function (signalId, tsUnix) {
        btn.addEventListener("click", function () {
          if (global.RagxStrategyPanel && global.RagxStrategyPanel.openRecentSignalsSection) {
            global.RagxStrategyPanel.openRecentSignalsSection();
          }
          if (global.RagxStrategyPanel && global.RagxStrategyPanel.highlightSignalIds) {
            global.RagxStrategyPanel.highlightSignalIds([signalId]);
          }
          if (global.RagxStrategyPanel && global.RagxStrategyPanel.focusChartAtUnix) {
            global.RagxStrategyPanel.focusChartAtUnix(tsUnix);
          }
        });
      })(String(sid), ct);

      list.appendChild(btn);
    }

    if (prevHi && prevHi.length && global.RagxStrategyPanel && global.RagxStrategyPanel.highlightSignalIds) {
      global.RagxStrategyPanel.highlightSignalIds(prevHi);
    }
  }

  function fetchHistoryNow() {
    var loading = el("recent-signals-loading");
    if (loading) loading.hidden = false;

    if (abortCtrl) abortCtrl.abort();
    abortCtrl = new AbortController();

    var url = "/api/signal-history?" + buildQueryString();
    global.fetch(url, { signal: abortCtrl.signal, cache: "no-store" })
      .then(function (res) {
        if (!res.ok) throw new Error(String(res.status));
        return res.json();
      })
      .then(function (data) {
        renderRows(data && data.rows);
      })
      .catch(function (err) {
        if (err && err.name === "AbortError") return;
        if (loading) loading.hidden = true;
        var empty = el("recent-signals-empty");
        var list = el("recent-signals-list");
        if (list) list.innerHTML = "";
        if (empty) {
          empty.hidden = false;
          empty.textContent = "Could not load signal history.";
        }
      });
  }

  function scheduleRefresh() {
    if (debounceTimer) global.clearTimeout(debounceTimer);
    debounceTimer = global.setTimeout(function () {
      debounceTimer = null;
      fetchHistoryNow();
    }, DEBOUNCE_MS);
  }

  function flushRefresh() {
    if (debounceTimer) {
      global.clearTimeout(debounceTimer);
      debounceTimer = null;
    }
    fetchHistoryNow();
  }

  function mountFilters() {
    var sel = el("recent-filter-tf");
    if (sel && sel.options.length <= 1 && global.RagxTimeframe && global.RagxTimeframe.ORDER) {
      var order = global.RagxTimeframe.ORDER;
      var i;
      for (i = 0; i < order.length; i++) {
        var iv = order[i];
        var opt = global.document.createElement("option");
        opt.value = iv;
        opt.textContent = iv;
        sel.appendChild(opt);
      }
    }

    var actionRoot = el("recent-filter-actions");
    if (actionRoot) {
      actionRoot.addEventListener("click", function (ev) {
        var t = ev.target;
        if (!t || t.getAttribute("data-action-filter") == null) return;
        var key = t.getAttribute("data-action-filter");
        if (key === "all") {
          filterActions = null;
        } else {
          if (!filterActions) filterActions = new Set();
          if (filterActions.has(key)) filterActions.delete(key);
          else filterActions.add(key);
          if (filterActions.size === 0) filterActions = null;
        }
        syncActionChipUi();
        flushRefresh();
      });
    }

    var tfSel = el("recent-filter-tf");
    if (tfSel) {
      tfSel.addEventListener("change", function () {
        filterTf = String(tfSel.value || "").trim();
        flushRefresh();
      });
    }

    var refBtn = el("recent-signals-refresh");
    if (refBtn) {
      refBtn.addEventListener("click", function () {
        flushRefresh();
      });
    }

    syncActionChipUi();
    syncTfSelect();
  }

  function mount() {
    mountFilters();
    flushRefresh();
  }

  global.RagxRecentSignalHistory = {
    mount: mount,
    scheduleRefresh: scheduleRefresh,
    flushRefresh: flushRefresh,
  };
})(typeof window !== "undefined" ? window : globalThis);
