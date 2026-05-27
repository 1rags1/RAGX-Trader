/**
 * RAGX-Trader — strategy guide: current call, why it happened, vote breakdown, recent markers
 *
 * Explanations come from the backend in plain English; `explanation_detail` shows in fold-outs.
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

  function signalClass(sig) {
    if (sig === "buy") return "sig-buy";
    if (sig === "sell") return "sig-sell";
    if (sig === "exit") return "sig-exit";
    return "sig-neutral";
  }

  function voteSummary(signal, confidence) {
    var sig = String(signal || "neutral").toLowerCase();
    var c = confidence != null && confidence !== "" ? String(confidence) : "—";
    if (sig === "buy") return "Leaning buy — rule strength " + c + "/100";
    if (sig === "sell") return "Leaning sell — rule strength " + c + "/100";
    return "No side — rule strength " + c + "/100 (this rule is sitting out)";
  }

  var lastDecisionIso = null;

  function formatDecisionTime(iso) {
    if (!iso || !String(iso).length) return "—";
    try {
      var d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso);
      return new Intl.DateTimeFormat("en-US", {
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
    } catch {
      return "—";
    }
  }

  function setDecisionTimestamp(iso) {
    if (iso != null && String(iso).trim()) lastDecisionIso = String(iso);
    refreshDecisionMeta();
  }

  function refreshDecisionMeta() {
    var tfEl = el("sidebar-chart-tf");
    if (tfEl && global.RagxTimeframe && typeof global.RagxTimeframe.getCurrent === "function") {
      tfEl.textContent = global.RagxTimeframe.getCurrent() || "—";
    }
    var lpEl = el("sidebar-live-price");
    var src = el("live-price-value");
    if (lpEl && src) lpEl.textContent = (src.textContent && String(src.textContent).trim()) || "—";
    setText("sidebar-decision-time", formatDecisionTime(lastDecisionIso));
  }

  function signalLeadFromFinal(final, sig) {
    var s = String(sig || "neutral").toLowerCase();
    var c = final && final.confidence;
    var n = Number(c);
    var hasC = c != null && c !== "" && Number.isFinite(n);
    var cPart = hasC ? " Rule strength is " + String(Math.round(n)) + " out of 100." : "";
    if (s === "buy") return "The model currently favors a long bias." + cPart;
    if (s === "sell") return "The model currently favors a short bias." + cPart;
    return (
      "The system is not picking a side yet — the weighted rules are balanced or the chart is still unclear." +
      cPart
    );
  }

  function confNum(final) {
    var n = Number(final && final.confidence);
    return Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round(n))) : 0;
  }

  /**
   * At-a-glance line: for BUY/SELL use strength-matched copy; else backend short_summary or lead.
   */
  function atAGlanceLine(payload, final, sig) {
    var s = String(sig || "").toLowerCase();
    var RS = global.RagxSignalStrength;
    if (RS && (s === "buy" || s === "sell")) {
      var line = RS.buildGlanceLine(s, confNum(final));
      if (line) return line;
    }
    var disp = payload && payload.explanation_display;
    if (disp && disp.short_summary != null && String(disp.short_summary).trim()) {
      return String(disp.short_summary).trim();
    }
    var lead = signalLeadFromFinal(final, sig);
    if (lead.length > 180) return lead.slice(0, 177).trim() + "…";
    return lead;
  }

  function renderStrengthChrome(payload, final, sig) {
    var RS = global.RagxSignalStrength;
    var c = confNum(final);
    var strEl = el("decision-strength-label");
    if (strEl) {
      if (RS) {
        var lbl = RS.getStrengthLabel(sig, c);
        if (lbl) {
          strEl.textContent = lbl;
          strEl.hidden = false;
        } else {
          strEl.textContent = "";
          strEl.hidden = true;
        }
      } else {
        strEl.textContent = "";
        strEl.hidden = true;
      }
    }
    var noteEl = el("decision-market-note");
    if (noteEl) {
      if (RS) {
        var ep = payload && payload.explanation_payload;
        noteEl.textContent = RS.getMarketContextNote(ep) || "";
      } else {
        noteEl.textContent = "";
      }
    }
  }

  function setDecisionBlockSignal(sig) {
    var block = el("decision-signal-block");
    if (!block) return;
    var s = String(sig || "neutral").toLowerCase();
    block.setAttribute("data-signal", s);
  }

  function fmtPlanPrice(n) {
    if (n == null || n === "" || Number.isNaN(Number(n))) return "—";
    var x = Number(n);
    if (Math.abs(x) >= 1000) return x.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (Math.abs(x) >= 1) return x.toFixed(4);
    return x.toFixed(6);
  }

  function renderSuggestedTradePlan(plan) {
    var wrap = el("trade-plan-wrap");
    if (!wrap) return;
    if (!plan || plan.entry == null) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    setText("trade-plan-disclaimer", plan.disclaimer_plain || "");
    setText("trade-plan-entry", fmtPlanPrice(plan.entry) + " USDT");
    setText("trade-plan-stop", fmtPlanPrice(plan.stop_loss) + " USDT");
    setText("trade-plan-tp", fmtPlanPrice(plan.take_profit) + " USDT");
    var rr = plan.risk_reward_ratio;
    setText(
      "trade-plan-rr",
      rr != null && rr !== "" ? "About " + String(rr) + " : 1 (reward to risk)" : "—"
    );
    var rp = plan.risk_price;
    var rw = plan.reward_price;
    var riskLine = el("trade-plan-risk-line");
    if (riskLine) {
      if (rp != null && rw != null) {
        riskLine.textContent =
          "Rough price risk ≈ " +
          fmtPlanPrice(rp) +
          " per unit vs target gain ≈ " +
          fmtPlanPrice(rw) +
          " (before fees and slippage).";
      } else {
        riskLine.textContent = "";
      }
    }
    setText("trade-plan-summary", plan.summary_plain || "");
    setText("trade-plan-detail", plan.detail_plain || "");
  }

  function findStrategyById(strategies, id) {
    if (!strategies || !id) return null;
    var i;
    for (i = 0; i < strategies.length; i++) {
      if (strategies[i].id === id) return strategies[i];
    }
    return null;
  }

  var chartFocusHandler = null;
  var markerHighlightHandler = null;
  var highlightedIds = [];
  var lastPayloadRef = null;

  function setChartFocusHandler(fn) {
    chartFocusHandler = typeof fn === "function" ? fn : null;
  }

  function setMarkerHighlightHandler(fn) {
    markerHighlightHandler = typeof fn === "function" ? fn : null;
  }

  function focusChartAtUnix(unixSec) {
    var t = Math.floor(Number(unixSec));
    if (!Number.isFinite(t) || t <= 0) return;
    if (chartFocusHandler) chartFocusHandler(t);
  }

  function getHighlightedSignalIds() {
    return highlightedIds.slice();
  }

  function openRecentSignalsSection() {
    var det = el("recent-signals-details");
    if (!det) return;
    det.open = true;
    try {
      det.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch {
      /* ignore */
    }
  }

  function highlightSignalIds(ids) {
    highlightedIds = Array.isArray(ids) ? ids.slice() : [];
    if (highlightedIds.length) openRecentSignalsSection();
    if (markerHighlightHandler) markerHighlightHandler(highlightedIds);
    var list = el("recent-signals-list");
    if (!list) return;
    var firstEl = null;
    var rows = list.querySelectorAll(".recent-sig-row");
    var j;
    for (j = 0; j < rows.length; j++) {
      var row = rows[j];
      var sid = row.getAttribute("data-signal-id") || "";
      var on = highlightedIds.indexOf(sid) !== -1;
      row.classList.toggle("recent-sig-row--active", on);
      row.setAttribute("aria-current", on ? "true" : "false");
      if (on && !firstEl) firstEl = row;
    }
    if (firstEl) {
      try {
        firstEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } catch {
        /* ignore */
      }
    }
  }

  function latestMarkerForStrategy(strategyId) {
    var markers = (lastPayloadRef && lastPayloadRef.signal_markers) || [];
    var best = null;
    var i;
    for (i = 0; i < markers.length; i++) {
      if (markers[i].strategy_source !== strategyId) continue;
      if (!best || Number(markers[i].timestamp) > Number(best.timestamp)) best = markers[i];
    }
    return best;
  }

  function renderMarketSummary(ms) {
    var head = el("market-summary-headline");
    var list = el("market-summary-list");
    if (!head || !list) return;
    if (!ms || typeof ms !== "object") {
      head.textContent = "—";
      list.innerHTML = "";
      return;
    }
    head.textContent = ms.headline != null && String(ms.headline).trim() ? String(ms.headline) : "—";
    list.innerHTML = "";
    var items = ms.items || [];
    var i;
    for (i = 0; i < items.length; i++) {
      var it = items[i];
      var li = global.document.createElement("li");
      li.className = "market-summary-item";
      var strong = global.document.createElement("strong");
      strong.className = "market-summary-label";
      strong.textContent = (it.title != null ? String(it.title) : "") + ": ";
      var span = global.document.createElement("span");
      span.className = "market-summary-text";
      span.textContent = it.text != null ? String(it.text) : "";
      li.appendChild(strong);
      li.appendChild(span);
      list.appendChild(li);
    }
  }

  function render(payload) {
    if (!payload || typeof payload !== "object") return;
    lastPayloadRef = payload;

    renderMarketSummary(payload.market_summary);

    var final = payload.final || {};
    var sig = final.signal || "neutral";
    var badge = el("strat-final-badge");
    if (badge) {
      badge.textContent = String(sig).toUpperCase();
      badge.className = "decision-signal-badge " + signalClass(sig);
    }
    setDecisionBlockSignal(sig);
    setText(
      "strat-final-confidence",
      final.confidence != null && final.confidence !== ""
        ? String(final.confidence) + " / 100"
        : "—"
    );
    renderStrengthChrome(payload, final, sig);
    setText("strat-signal-lead", signalLeadFromFinal(final, sig));

    var glanceEl = el("decision-short-expl");
    if (glanceEl) {
      glanceEl.textContent = atAGlanceLine(payload, final, sig);
    }

    var techFold = el("strat-final-technical");
    var techBody = el("strat-final-detail");
    var rawFinal = final.explanation_detail;
    if (techFold && techBody) {
      if (rawFinal != null && String(rawFinal).trim()) {
        techFold.hidden = false;
        techBody.textContent = String(rawFinal);
      } else {
        techFold.hidden = true;
        techBody.textContent = "";
      }
    }

    var explEl = el("strat-final-explanation");
    if (explEl) {
      var whyCore = final.explanation != null && final.explanation !== "" ? String(final.explanation) : "—";
      if (whyCore !== "—" && global.RagxSignalStrength && typeof global.RagxSignalStrength.applyWhyPrefix === "function") {
        whyCore = global.RagxSignalStrength.applyWhyPrefix(sig, confNum(final), whyCore);
      }
      explEl.textContent = whyCore;
      explEl.className = "decision-why-text";
      explEl.classList.remove("strat-explanation--linkable");
      explEl.onclick = null;
      explEl.onkeydown = null;
      explEl.removeAttribute("title");
      explEl.removeAttribute("role");
      explEl.removeAttribute("tabindex");
      var comb = latestMarkerForStrategy("combined_signal");
      if (comb && (comb.signal_id || comb.id)) {
        explEl.classList.add("strat-explanation--linkable");
        explEl.setAttribute("role", "button");
        explEl.setAttribute("tabindex", "0");
        explEl.setAttribute("title", "Jump to the latest sticker on the chart");
        explEl.onkeydown = function (ev) {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            explEl.click();
          }
        };
        explEl.onclick = function () {
          var id = String(comb.signal_id || comb.id);
          openRecentSignalsSection();
          highlightSignalIds([id]);
          focusChartAtUnix(comb.timestamp);
        };
      }
    }

    var disp = payload.explanation_display;
    var sumEl = el("strat-explanation-summary");
    var begFold = el("strat-beginner-fold");
    var begBody = el("strat-beginner-body");
    var toneClasses = [
      "strat-tone--strong_warning",
      "strat-tone--caution",
      "strat-tone--balanced",
      "strat-tone--confident_action",
      "strat-tone--neutral",
    ];
    function clearExplanationTone(eln) {
      if (!eln) return;
      var tc;
      for (tc = 0; tc < toneClasses.length; tc++) {
        eln.classList.remove(toneClasses[tc]);
      }
    }
    function applyToneToGlance(targetEl) {
      if (!targetEl) return;
      clearExplanationTone(targetEl);
      if (disp && disp.short_summary != null && String(disp.short_summary).trim()) {
        var tone = disp.guidance_tone;
        if (tone === "strong_warning") targetEl.classList.add("strat-tone--strong_warning");
        else if (tone === "caution") targetEl.classList.add("strat-tone--caution");
        else if (tone === "confident_action") targetEl.classList.add("strat-tone--confident_action");
        else if (tone === "balanced") targetEl.classList.add("strat-tone--balanced");
        else targetEl.classList.add("strat-tone--neutral");
        return;
      }
      var RS = global.RagxSignalStrength;
      var sGl = String(sig || "").toLowerCase();
      if (RS && (sGl === "buy" || sGl === "sell")) {
        var b = RS.band(confNum(final));
        if (b === "high") targetEl.classList.add("strat-tone--confident_action");
        else if (b === "medium") targetEl.classList.add("strat-tone--balanced");
        else targetEl.classList.add("strat-tone--caution");
        return;
      }
      targetEl.classList.add("strat-tone--neutral");
    }

    if (sumEl) {
      clearExplanationTone(sumEl);
      if (disp && disp.short_summary != null && String(disp.short_summary).trim()) {
        sumEl.hidden = false;
        sumEl.textContent = String(disp.short_summary);
        var tone = disp.guidance_tone;
        if (tone === "strong_warning") sumEl.classList.add("strat-tone--strong_warning");
        else if (tone === "caution") sumEl.classList.add("strat-tone--caution");
        else if (tone === "confident_action") sumEl.classList.add("strat-tone--confident_action");
        else if (tone === "balanced") sumEl.classList.add("strat-tone--balanced");
        else sumEl.classList.add("strat-tone--neutral");
      } else {
        sumEl.hidden = true;
        sumEl.textContent = "";
      }
    }
    applyToneToGlance(glanceEl);
    if (begFold && begBody) {
      if (disp && disp.beginner_explanation != null && String(disp.beginner_explanation).trim()) {
        begFold.hidden = false;
        begBody.textContent = String(disp.beginner_explanation);
      } else {
        begFold.hidden = true;
        begBody.textContent = "";
      }
    }

    var warm = el("strat-warmup");
    if (warm) warm.hidden = !!payload.sufficient_data;

    var items = payload.strategies || [];
    var struct = findStrategyById(items, "price_structure");
    var structPlain = el("ind-structure-plain");
    var structDet = el("ind-structure-detail");
    if (structPlain) {
      structPlain.textContent =
        struct && struct.explanation != null && String(struct.explanation).trim()
          ? String(struct.explanation)
          : "Swing structure bias will show here once the structure rule has enough bars.";
    }
    if (structDet) {
      structDet.textContent =
        struct && struct.explanation_detail != null && String(struct.explanation_detail).trim()
          ? String(struct.explanation_detail)
          : "—";
    }

    renderSuggestedTradePlan(payload.suggested_trade_plan);

    var list = el("strat-raw-breakdown-list");
    if (list) {
      list.innerHTML = "";
      items.forEach(function (s) {
        var row = global.document.createElement("div");
        row.className = "strat-row";
        row.setAttribute("data-strategy-id", s.id || "");
        var name = global.document.createElement("div");
        name.className = "strat-row-name";
        name.textContent = s.name || s.id || "Rule";
        var vote = global.document.createElement("div");
        vote.className = "strat-row-vote " + signalClass(s.signal);
        vote.textContent = voteSummary(s.signal, s.confidence);
        var note = global.document.createElement("div");
        note.className = "strat-row-note";
        note.textContent = s.explanation != null ? String(s.explanation) : "";
        row.appendChild(name);
        row.appendChild(vote);
        row.appendChild(note);

        var detStr = s.explanation_detail;
        if (detStr != null && String(detStr).trim()) {
          var detWrap = global.document.createElement("details");
          detWrap.className = "nested-details strat-row-technical";
          var sum = global.document.createElement("summary");
          sum.textContent = "Rule details (raw values)";
          var body = global.document.createElement("div");
          body.className = "strat-row-detail-body";
          body.textContent = String(detStr);
          detWrap.appendChild(sum);
          detWrap.appendChild(body);
          row.appendChild(detWrap);
        }

        list.appendChild(row);
      });
    }

    refreshDecisionMeta();
  }

  global.RagxStrategyPanel = {
    render: render,
    setChartFocusHandler: setChartFocusHandler,
    setMarkerHighlightHandler: setMarkerHighlightHandler,
    highlightSignalIds: highlightSignalIds,
    getHighlightedSignalIds: getHighlightedSignalIds,
    openRecentSignalsSection: openRecentSignalsSection,
    focusChartAtUnix: focusChartAtUnix,
    setDecisionTimestamp: setDecisionTimestamp,
    refreshDecisionMeta: refreshDecisionMeta,
  };
})(typeof window !== "undefined" ? window : globalThis);
