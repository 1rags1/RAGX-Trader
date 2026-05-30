/**
 * Investor dashboard interactions (search + selected stock panel).
 * Live data: FINNHUB_API_KEY (quotes, profile, news) + optional TWELVE_DATA_API_KEY (closing-price chart fallback).
 */
(function () {
  "use strict";

  function text(v) {
    return v === null || v === undefined || v === "" ? "—" : String(v);
  }

  function escapeHtml(raw) {
    return String(raw)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /** Only use in href="" after escaping attribute value. */
  function isLikelyHttpUrl(u) {
    return /^https?:\/\//i.test(String(u || "").trim());
  }

  function periodReturnPercentFromVals(vals) {
    if (!vals || vals.length < 2) return null;
    const a = vals[0];
    const b = vals[vals.length - 1];
    if (!Number.isFinite(a) || !Number.isFinite(b) || a <= 0) return null;
    return ((b - a) / a) * 100;
  }

  /** Green / red / neutral — matches Stocks-style thresholds */
  function trendStrokeClassFromPct(pct) {
    if (pct === null || !Number.isFinite(pct)) return "neutral";
    if (Math.abs(pct) < 0.02) return "neutral";
    return pct > 0 ? "up" : "down";
  }

  const INVESTOR_OUTLOOK_TITLE = "Long-Term Outlook";
  const INVESTOR_RESEARCH_DISCLAIMER = "This is research support, not financial advice.";

  const INVESTOR_SCORE_PILLARS = [
    { key: "long_term_trend_pts", label: "Long-term trend", max: 25 },
    { key: "company_quality_pts", label: "Company quality", max: 20 },
    { key: "news_narrative_pts", label: "News sentiment", max: 20 },
    { key: "risk_control_pts", label: "Risk", max: 15 },
    { key: "momentum_consistency_pts", label: "Consistency", max: 10 },
    { key: "data_confidence_pts", label: "Data confidence", max: 10 },
  ];

  function normalizeInvestorRating(rating) {
    const r = String(rating || "").trim();
    const rl = r.toLowerCase();
    if (rl.includes("strong") && rl.includes("watch")) return "Strong Watch";
    if (rl.includes("high risk") || (rl.includes("avoid") && rl.includes("risk"))) return "High Risk";
    if (rl.includes("watch")) return "Watch";
    if (rl.includes("cautious")) return "Cautious";
    if (rl.includes("neutral")) return "Neutral";
    if (rl.includes("bull")) return "Strong Watch";
    if (rl.includes("bear")) return "High Risk";
    return r || "Neutral";
  }

  function investorRatingVariant(rating) {
    const label = normalizeInvestorRating(rating);
    const rl = label.toLowerCase();
    if (rl.includes("strong") && rl.includes("watch")) return "strong_watch";
    if (rl.includes("high risk")) return "high_risk";
    if (rl.includes("watch")) return "watch";
    if (rl.includes("cautious")) return "cautious";
    if (rl.includes("neutral")) return "neutral";
    return "neutral";
  }

  function applyInvestorRatingBadge(el, rating) {
    if (!el) return;
    const label = normalizeInvestorRating(rating);
    const variant = investorRatingVariant(rating);
    el.textContent = label;
    el.className = `investor-rating-badge investor-rating-badge--${variant}`;
  }

  function formatPillarPoints(raw, maxPts) {
    const n = Number(raw);
    if (!Number.isFinite(n)) return "—";
    return `${n.toFixed(n % 1 === 0 ? 0 : 1)}/${maxPts}`;
  }

  function renderInvestorPillarBreakdown(container, breakdown, pillarsFromApi) {
    if (!container) return;
    container.innerHTML = "";
    const rows = Array.isArray(pillarsFromApi) && pillarsFromApi.length ? pillarsFromApi : null;
    if (rows) {
      rows.forEach((p) => {
        if (!p || typeof p !== "object") return;
        const row = document.createElement("div");
        row.className = "investor-pillar-row";
        row.setAttribute("role", "listitem");
        const lab = document.createElement("span");
        lab.className = "investor-pillar-label";
        lab.textContent = String(p.label || "—");
        const val = document.createElement("span");
        val.className = "investor-pillar-val";
        val.textContent = formatPillarPoints(p.points, p.max_points);
        row.appendChild(lab);
        row.appendChild(val);
        container.appendChild(row);
      });
      return;
    }
    const b = breakdown || {};
    INVESTOR_SCORE_PILLARS.forEach((p) => {
      const row = document.createElement("div");
      row.className = "investor-pillar-row";
      row.setAttribute("role", "listitem");
      const lab = document.createElement("span");
      lab.className = "investor-pillar-label";
      lab.textContent = p.label;
      const val = document.createElement("span");
      val.className = "investor-pillar-val";
      val.textContent = formatPillarPoints(b[p.key], p.max);
      row.appendChild(lab);
      row.appendChild(val);
      container.appendChild(row);
    });
  }

  function thesisRatingMeta(rating) {
    const label = normalizeInvestorRating(rating);
    return { label, variant: investorRatingVariant(rating) };
  }

  function renderThesisBulletList(listEl, items, emptyLabel) {
    if (!listEl) return;
    listEl.innerHTML = "";
    const rows = Array.isArray(items) ? items.filter((x) => typeof x === "string" && x.trim()) : [];
    if (!rows.length) {
      const li = document.createElement("li");
      li.textContent = emptyLabel || "—";
      listEl.appendChild(li);
      return;
    }
    rows.slice(0, 3).forEach((line) => {
      const li = document.createElement("li");
      li.textContent = line.trim();
      listEl.appendChild(li);
    });
  }

  function formatSignedPeriodPct(pct) {
    if (pct === null || !Number.isFinite(pct)) return "—";
    const sign = pct > 0 ? "+" : "";
    return `${sign}${pct.toFixed(2)}%`;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  const INVESTOR_CHART_W = 640;
  const INVESTOR_CHART_H = 248;

  function friendlyInvestorChartError(raw) {
    const m = String(raw || "").trim();
    if (!m) return "Historical chart data is unavailable right now.";
    if (
      /twelve data|requires twelve|historical chart requires/i.test(m) ||
      (/finnhub|403|don't have access|historical closes unavailable|closing-price history/i.test(m) &&
        !/twelve data/i.test(m))
    ) {
      return "Historical chart requires Twelve Data API key.";
    }
    return m.length > 120 ? `${m.slice(0, 117)}…` : m;
  }

  /** Reject null/undefined/blank before Number() — Number(null) is 0. */
  function investorPresentNumber(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === "string" && !v.trim()) return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  /** Plain closing price line chart (historical closes only). */
  function formatUsdPriceInvestor(n) {
    if (!Number.isFinite(n)) return "—";
    const frac = Math.abs(n) >= 250 ? 2 : Math.abs(n) >= 25 ? 2 : 4;
    return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: frac })}`;
  }

  function formatSignedRangeDollar(delta) {
    if (!Number.isFinite(delta)) return "—";
    if (delta === 0) return "+$0.00";
    const sign = delta > 0 ? "+" : "-";
    const v = `$${Math.abs(delta).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    return `${sign}${v}`;
  }

  /** Map point → unix seconds (Finnhub / Twelve use seconds). */
  function pointUnixTime(p) {
    if (!p || typeof p !== "object") return NaN;
    const raw = p.time !== undefined && p.time !== null ? p.time : p.t;
    if (typeof raw === "number" && Number.isFinite(raw)) return raw > 2e11 ? Math.floor(raw / 1000) : Math.floor(raw);
    if (typeof raw === "string" && /^\d+$/.test(raw.trim())) {
      const n = parseInt(raw, 10);
      return Number.isFinite(n) ? (n > 2e11 ? Math.floor(n / 1000) : n) : NaN;
    }
    return NaN;
  }

  function applyInvestorDeltaTone(el, trend) {
    if (!el) return;
    el.classList.remove("investor-chart-delta--up", "investor-chart-delta--down", "investor-chart-delta--neutral");
    const v = trend === "up" || trend === "down" || trend === "neutral" ? trend : "neutral";
    el.classList.add(`investor-chart-delta--${v}`);
  }

  const WATCHLIST_STORAGE_KEY = "ragx_investor_watchlist";
  const WATCHLIST_MAX_ITEMS = 20;

  function loadWatchlistItems() {
    try {
      if (typeof localStorage === "undefined") return [];
      const raw = localStorage.getItem(WATCHLIST_STORAGE_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      const items = Array.isArray(parsed?.items) ? parsed.items : Array.isArray(parsed) ? parsed : [];
      return items
        .filter((x) => x && typeof x.ticker === "string" && x.ticker.trim())
        .map((x) => ({
          ticker: String(x.ticker).trim().toUpperCase(),
          company_name: typeof x.company_name === "string" ? x.company_name.trim() : "",
          added_at: typeof x.added_at === "string" ? x.added_at : new Date().toISOString(),
        }))
        .slice(0, WATCHLIST_MAX_ITEMS);
    } catch {
      return [];
    }
  }

  function saveWatchlistItems(items) {
    try {
      if (typeof localStorage === "undefined") return;
      localStorage.setItem(
        WATCHLIST_STORAGE_KEY,
        JSON.stringify({ version: 1, items: items.slice(0, WATCHLIST_MAX_ITEMS) })
      );
    } catch {
      /* ignore quota / private mode */
    }
  }

  function truncateOutlook(text, maxLen) {
    const t = String(text || "").trim();
    if (!t) return `${INVESTOR_OUTLOOK_TITLE} — research summary loading…`;
    if (t.length <= maxLen) return t;
    return `${t.slice(0, maxLen - 1).trim()}…`;
  }

  function watchlistRatingVariant(rating) {
    return investorRatingVariant(rating);
  }

  function buildSparkPolylineAttr(vals, width, height, padX, padY) {
    if (vals.length < 2) return "";
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const range = Math.max(max - min, 1e-9);
    const uw = width - 2 * padX;
    const uh = height - 2 * padY;
    return vals
      .map((v, i) => {
        const x = padX + (i / (vals.length - 1)) * uw;
        const y = padY + (1 - (v - min) / range) * uh;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }

  function formatInvestorSignedPct(pct) {
    const n = investorPresentNumber(pct);
    if (n === null) return "—";
    const sign = n >= 0 ? "+" : "−";
    return `${sign}${Math.abs(n).toFixed(2)}%`;
  }

  function deltaToneClass(pct) {
    const n = investorPresentNumber(pct);
    if (n === null) return "muted";
    if (Math.abs(n) < 0.02) return "muted";
    return n >= 0 ? "up" : "down";
  }

  function providerStatusClass(status) {
    const s = String(status || "").toLowerCase();
    if (s === "live") return "investor-diag-status investor-diag-status--live";
    if (s === "demo" || s === "mock") return "investor-diag-status investor-diag-status--demo";
    if (s === "missing_keys") return "investor-diag-status investor-diag-status--missing_keys";
    return "investor-diag-status";
  }

  function applyInvestorDiagnostics(j) {
    const banner = document.getElementById("investor-mode-banner");
    const sub = document.getElementById("investor-dash-subtitle");
    const panel = document.getElementById("investor-diagnostics-panel");
    if (!banner || !panel) return;

    const variant = j?.badge_variant === "live" ? "live" : "demo";
    banner.className = `investor-mode-banner investor-mode-banner--${variant}`;
    banner.textContent =
      j?.badge_label || "Demo Mode / Missing API Keys";

    if (sub) {
      sub.textContent = j?.fully_live
        ? "Live investor feeds are connected — scores, charts, and news use configured APIs."
        : "Demo mode or missing keys — add FINNHUB_API_KEY (+ optional TWELVE_DATA_API_KEY as chart fallback) and a news key for live mode.";
    }

    const rowHtml = (label, blk) => {
      const pid = blk && blk.provider_id != null ? String(blk.provider_id) : "—";
      const st = blk && blk.status != null ? String(blk.status) : "unknown";
      const det = blk && blk.detail ? String(blk.detail) : "";
      const last = blk && blk.last_success_fetch_utc ? String(blk.last_success_fetch_utc) : "Never (no successful pull recorded yet)";
      const errLine = (e) => {
        if (!e || typeof e !== "object") return "";
        const m = typeof e.message === "string" ? e.message.trim() : "";
        const d = typeof e.detail === "string" ? e.detail.trim() : "";
        if (!m && !d) return "";
        const body = [m, d].filter(Boolean).join(" — ").slice(0, 920);
        return `<div class="investor-diag-proverr">${escapeHtml(body)}</div>`;
      };
      let extraErr = "";
      if (blk?.last_profile_error && typeof blk.last_profile_error === "object") {
        const lp = blk.last_profile_error;
        const lm = typeof lp.message === "string" ? lp.message.trim() : "request failed";
        extraErr += errLine({ ...lp, message: `Profile: ${lm}` });
      }
      return `
        <tr>
          <th scope="row">${escapeHtml(label)}</th>
          <td>
            <div class="investor-diag-celltop">
              <strong>${escapeHtml(pid)}</strong>
              <span class="${providerStatusClass(st)}">${escapeHtml(st)}</span>
            </div>
            <div class="investor-diag-detail">${escapeHtml(det)}</div>
            ${errLine(blk?.last_error)}
            ${extraErr}
            <div class="investor-diag-fetch">${escapeHtml(last)}</div>
          </td>
        </tr>`;
    };

    const serverT = j?.server_time_utc ? String(j.server_time_utc) : "—";

    const keys = j?.api_keys || {};
    const finnhubKey = keys?.FINNHUB_API_KEY ? "detected" : "missing";
    const twelveKey = keys?.TWELVE_DATA_API_KEY ? "detected" : "not set";
    const alphaKey = keys?.ALPHA_VANTAGE_API_KEY ? "detected" : "missing";
    const newsapiKey = keys?.NEWSAPI_API_KEY ? "detected" : "missing";
    const newsEnvKey = keys?.news_provider_env_key ? String(keys.news_provider_env_key) : "provider-dependent";
    const latestErr =
      j?.price_provider?.last_error?.message ||
      j?.chart_provider?.last_error?.message ||
      j?.news_provider?.last_error?.message ||
      "";
    const latestErrDetail =
      j?.price_provider?.last_error?.detail ||
      j?.chart_provider?.last_error?.detail ||
      j?.news_provider?.last_error?.detail ||
      "";
    const latestErrBody = latestErr
      ? [latestErr ? `Latest API error: ${latestErr}` : "", latestErrDetail ? `(${latestErrDetail})` : ""].filter(Boolean).join(" ")
      : "";

    panel.innerHTML = `
      <p class="investor-diag-server">Server time (UTC): <strong>${escapeHtml(serverT)}</strong></p>

      <p class="investor-diag-detail">
        API keys: FINNHUB_API_KEY=${escapeHtml(String(finnhubKey))} · TWELVE_DATA_API_KEY=${escapeHtml(String(twelveKey))}
        · ALPHA_VANTAGE_API_KEY=${escapeHtml(String(alphaKey))} · NEWSAPI_API_KEY=${escapeHtml(String(newsapiKey))}.
        News env key: ${escapeHtml(String(newsEnvKey))}.
      </p>

      ${latestErrBody ? `<div class="investor-diag-proverr">${escapeHtml(latestErrBody)}</div>` : ""}

      <table class="investor-diag-table">
        <tbody>
          ${rowHtml("Price provider", j?.price_provider)}
          ${rowHtml("Chart provider", j?.chart_provider)}
          ${rowHtml("News provider", j?.news_provider)}
        </tbody>
      </table>
    `;
  }

  async function refreshInvestorDiagnostics() {
    try {
      const res = await fetch("/api/investor/diagnostics");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const j = await res.json();
      applyInvestorDiagnostics(j);
    } catch {
      const banner = document.getElementById("investor-mode-banner");
      const panel = document.getElementById("investor-diagnostics-panel");
      if (banner) {
        banner.className = "investor-mode-banner investor-mode-banner--demo";
        banner.textContent = "Demo Mode / Missing API Keys — diagnostics request failed";
      }
      if (panel) {
        panel.innerHTML = `<p class="investor-diag-error">Diagnostics could not load. Confirm the backend is running and try again.</p>`;
      }
    }
  }

  function mountInvestorDiagnosticsRouting() {
    void refreshInvestorDiagnostics();
    document.addEventListener("ragx-active-tab", (ev) => {
      if ((ev.detail || {}).tab === "investor") void refreshInvestorDiagnostics();
    });
  }

  function mountInvestorSearch() {
    const input = document.getElementById("investor-stock-search");
    const button = document.getElementById("investor-search-btn");
    const status = document.getElementById("investor-search-status");
    const results = document.getElementById("investor-search-results");
    const selectedTicker = document.getElementById("investor-selected-ticker");
    const selectedCompany = document.getElementById("investor-selected-company");
    const selectedExchange = document.getElementById("investor-selected-exchange");
    const selectedAssetType = document.getElementById("investor-selected-asset-type");
    const selectedScore = document.getElementById("investor-selected-score");
    const selectedRating = document.getElementById("investor-selected-rating");
    const selectedBreakdown = document.getElementById("investor-selected-breakdown");
    const selectedExplanation = document.getElementById("investor-selected-explanation");
    const selectedRisk = document.getElementById("investor-selected-risk");
    const thesisBadge = document.getElementById("investor-thesis-badge");
    const thesisWhy = document.getElementById("investor-thesis-why");
    const thesisStrengths = document.getElementById("investor-thesis-strengths");
    const thesisRisks = document.getElementById("investor-thesis-risks");
    const thesisSummary = document.getElementById("investor-thesis-summary");
    const oppsStatus = document.getElementById("investor-opps-status");
    const oppsList = document.getElementById("investor-opps-list");
    const rangeButtons = Array.from(document.querySelectorAll(".investor-range-btn[data-range]"));
    const chartLine = document.getElementById("investor-selected-chart-line");
    const chartArea = document.getElementById("investor-selected-chart-area");
    const chartSvg = document.getElementById("investor-selected-chart");
    const chartHoverLayer = document.getElementById("investor-chart-hover-layer");
    const chartCrosshair = document.getElementById("investor-chart-crosshair-v");
    const chartHoverDot = document.getElementById("investor-chart-hover-dot");
    const chartTooltip = document.getElementById("investor-chart-tooltip");
    const chartTooltipPrice = document.getElementById("investor-chart-tooltip-price");
    const chartTooltipDate = document.getElementById("investor-chart-tooltip-date");
    const chartGrid = document.getElementById("investor-chart-grid");
    const chartYLabels = document.getElementById("investor-chart-y-labels");
    const chartXLabels = document.getElementById("investor-chart-x-labels");
    const chartClipRect = document.getElementById("investor-chart-clip-rect");
    const chartEmpty = document.getElementById("investor-chart-empty");
    const chartWrap = document.getElementById("investor-chart-wrap");
    const chartRangeLabel = document.getElementById("investor-chart-range-label");
    const chartLastPrice = document.getElementById("investor-chart-last-price");
    const chartDeltaDollar = document.getElementById("investor-chart-delta-dollar");
    const chartDeltaPct = document.getElementById("investor-chart-delta-pct");
    const chartDebugEl = document.getElementById("investor-chart-debug-detail");
    const newsStatus = document.getElementById("investor-news-status");
    const newsList = document.getElementById("investor-news-list");
    const insiderSummary = document.getElementById("investor-insider-summary");
    const insiderList = document.getElementById("investor-insider-list");
    const watchlistGrid = document.getElementById("investor-watchlist-grid");
    const watchlistEmpty = document.getElementById("investor-watchlist-empty");

    const applyThesisPayload = (payload) => {
      const thesis = payload?.thesis || {};
      const score = payload?.score || {};
      const ratingMeta = thesisRatingMeta(thesis.overall_rating_display || thesis.overall_rating || score.rating);

      if (thesisBadge) {
        thesisBadge.textContent = ratingMeta.label;
        thesisBadge.className = `investor-rating-badge investor-rating-badge--${ratingMeta.variant}`;
      }
      if (thesisWhy) {
        thesisWhy.textContent =
          text(thesis.why_ranked) !== "—" ? text(thesis.why_ranked) : text(score.explanation) || text(score.why_ranked);
      }
      renderThesisBulletList(
        thesisStrengths,
        thesis.key_strengths || (payload?.sections || {}).what_could_help,
        "No clear strengths surfaced on this window."
      );
      renderThesisBulletList(
        thesisRisks,
        thesis.key_risks || (payload?.sections || {}).what_could_hurt,
        "No specific risks flagged beyond normal market volatility."
      );
      if (thesisSummary) {
        thesisSummary.textContent =
          text(thesis.short_summary) !== "—"
            ? text(thesis.short_summary)
            : text((payload?.sections || {}).overall_conclusion);
      }
    };

    const setThesisLoading = () => {
      if (thesisBadge) {
        thesisBadge.textContent = "…";
        thesisBadge.className = "investor-rating-badge investor-rating-badge--neutral";
      }
      if (thesisWhy) thesisWhy.textContent = "Building long-term research thesis…";
      renderThesisBulletList(thesisStrengths, [], "Analyzing…");
      renderThesisBulletList(thesisRisks, [], "Analyzing…");
      if (thesisSummary) thesisSummary.textContent = "Summarizing long-term drivers…";
    };

    const setThesisError = () => {
      if (thesisBadge) {
        thesisBadge.textContent = "—";
        thesisBadge.className = "investor-rating-badge investor-rating-badge--neutral";
      }
      if (thesisWhy) thesisWhy.textContent = "Thesis unavailable — try again after data refresh.";
      renderThesisBulletList(thesisStrengths, [], "—");
      renderThesisBulletList(thesisRisks, [], "—");
      if (thesisSummary) thesisSummary.textContent = "Keep this ticker on watch until live data reconnects.";
    };

    const isInvestorChartDebug = () => {
      try {
        if (typeof window !== "undefined" && window.RAGX_INVESTOR_DEBUG === true) return true;
        if (typeof URLSearchParams !== "undefined") {
          const q = new URLSearchParams(window.location.search).get("investor_debug");
          if (q === "1" || q === "true") return true;
        }
        if (typeof localStorage !== "undefined" && localStorage.getItem("ragx_investor_debug") === "1") return true;
      } catch {
        /* ignore */
      }
      return false;
    };

    const searchMounted = !!(input && button && status && results);
    if (!searchMounted) {
      console.warn("[Investor] Search UI incomplete; chart and opportunities still load.");
    }

    let selected = null;
    let selectedFromSearch = false;
    let currentRange = "1D";
    let opportunitiesIntervalLabel = "6M";
    let chartPlotState = null;

    const syncChartRangeLabel = () => {
      if (chartRangeLabel) chartRangeLabel.textContent = currentRange;
    };

    const formatInvestorTickTime = (unixSec, range) => {
      if (!Number.isFinite(unixSec)) return "";
      const d = new Date(unixSec * 1000);
      if (range === "1D" || range === "5D") {
        return d.toLocaleString(undefined, {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        });
      }
      if (range === "1M" || range === "6M") {
        return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
      }
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "2-digit" });
    };

    const hideChartHover = () => {
      if (chartCrosshair) chartCrosshair.setAttribute("visibility", "hidden");
      if (chartHoverDot) chartHoverDot.setAttribute("visibility", "hidden");
      if (chartTooltip) chartTooltip.hidden = true;
    };

    const clearInvestorSvgLayers = () => {
      if (chartGrid) {
        chartGrid.innerHTML = "";
        chartGrid.removeAttribute("clip-path");
      }
      if (chartYLabels) chartYLabels.innerHTML = "";
      if (chartXLabels) chartXLabels.innerHTML = "";
      if (chartArea) {
        chartArea.setAttribute("d", "");
        chartArea.setAttribute("class", "investor-selected-chart-area investor-selected-chart-area--neutral");
      }
      if (chartLine) {
        chartLine.setAttribute("points", "");
        chartLine.removeAttribute("stroke-dasharray");
        chartLine.style.strokeDashoffset = "";
        chartLine.classList.remove("investor-selected-chart-line--draw");
        chartLine.setAttribute("class", "investor-selected-chart-line investor-selected-chart-line--neutral");
      }
      if (chartClipRect) {
        chartClipRect.setAttribute("x", "0");
        chartClipRect.setAttribute("y", "0");
        chartClipRect.setAttribute("width", String(INVESTOR_CHART_W));
        chartClipRect.setAttribute("height", String(INVESTOR_CHART_H));
      }
      hideChartHover();
      chartPlotState = null;
    };

    const neutralQuoteStrip = () => {
      syncChartRangeLabel();
      if (chartLastPrice) chartLastPrice.textContent = "—";
      if (chartDeltaDollar) {
        chartDeltaDollar.textContent = "—";
        applyInvestorDeltaTone(chartDeltaDollar, "neutral");
      }
      if (chartDeltaPct) {
        chartDeltaPct.textContent = "—";
        applyInvestorDeltaTone(chartDeltaPct, "neutral");
      }
    };

    const loadingQuoteStrip = () => {
      syncChartRangeLabel();
      const ell = "\u2026";
      if (chartLastPrice) chartLastPrice.textContent = ell;
      if (chartDeltaDollar) {
        chartDeltaDollar.textContent = ell;
        applyInvestorDeltaTone(chartDeltaDollar, "neutral");
      }
      if (chartDeltaPct) {
        chartDeltaPct.textContent = ell;
        applyInvestorDeltaTone(chartDeltaPct, "neutral");
      }
    };

    const setInvestorQuoteStripFromRange = (lastPx, deltaDollar, periodPct, trend) => {
      syncChartRangeLabel();
      if (chartLastPrice) chartLastPrice.textContent = formatUsdPriceInvestor(lastPx);
      if (chartDeltaDollar) {
        chartDeltaDollar.textContent = formatSignedRangeDollar(deltaDollar);
        applyInvestorDeltaTone(chartDeltaDollar, trend);
      }
      if (chartDeltaPct) {
        chartDeltaPct.textContent = formatSignedPeriodPct(periodPct);
        applyInvestorDeltaTone(chartDeltaPct, trend);
      }
    };

    syncChartRangeLabel();

    const setStatus = (message, visible) => {
      if (!status) return;
      status.textContent = message || "";
      status.hidden = !visible;
    };

    const loadInvestorProfile = async (symbol) => {
      if (!symbol || !selectedCompany || !selectedExchange || !selectedAssetType) return;
      try {
        const res = await fetch(`/api/investor/profile?symbol=${encodeURIComponent(symbol)}`);
        let p = null;
        try {
          p = await res.json();
        } catch {
          p = null;
        }
        if (!p || p.error) return;
        if (typeof p.company_name === "string" && p.company_name.trim()) {
          selectedCompany.textContent = p.company_name.trim();
        }
        if (typeof p.exchange === "string" && p.exchange.trim()) {
          selectedExchange.textContent = p.exchange.trim();
        }
        if (typeof p.asset_type === "string" && p.asset_type.trim()) {
          selectedAssetType.textContent = p.asset_type.trim();
        }
      } catch {
        /* keep search placeholder fields */
      }
    };

    const setSelected = (item, options) => {
      selected = item || null;
      selectedFromSearch = !!(options && options.fromSearch);
      if (!selectedTicker || !selectedCompany || !selectedExchange || !selectedAssetType) return;
      selectedTicker.textContent = text(item?.ticker);
      selectedCompany.textContent = text(item?.company_name);
      selectedExchange.textContent = text(item?.exchange);
      selectedAssetType.textContent = text(item?.asset_type);
      if (item?.ticker) {
        void loadInvestorProfile(item.ticker);
        void loadSelectedChart(item.ticker, currentRange);
        void loadNews(item.ticker);
        void loadInsiderActivity(item.ticker);
        void loadScore(item.ticker);
        void loadResearchSummary(item.ticker);
      }
    };

    const isOnWatchlist = (ticker) => {
      const sym = String(ticker || "")
        .trim()
        .toUpperCase();
      if (!sym) return false;
      return loadWatchlistItems().some((x) => x.ticker === sym);
    };

    const watchlistStarSvg = () =>
      `<svg class="investor-watchlist-star-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z"/></svg>`;

    const syncSearchResultStars = () => {
      if (!results) return;
      results.querySelectorAll(".investor-watchlist-star").forEach((btn) => {
        const sym = btn.dataset.ticker || "";
        const on = isOnWatchlist(sym);
        btn.classList.toggle("investor-watchlist-star--active", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
        btn.setAttribute(
          "aria-label",
          on ? `Remove ${sym} from My Watchlist` : `Add ${sym} to My Watchlist`
        );
      });
    };

    const toggleWatchlistItem = (item) => {
      const sym = String(item?.ticker || "")
        .trim()
        .toUpperCase();
      if (!sym) return;
      let items = loadWatchlistItems();
      if (items.some((x) => x.ticker === sym)) {
        items = items.filter((x) => x.ticker !== sym);
      } else {
        items.unshift({
          ticker: sym,
          company_name: typeof item?.company_name === "string" ? item.company_name.trim() : "",
          added_at: new Date().toISOString(),
        });
      }
      saveWatchlistItems(items);
      syncSearchResultStars();
      void renderWatchlist();
    };

    const removeFromWatchlist = (ticker) => {
      const sym = String(ticker || "")
        .trim()
        .toUpperCase();
      if (!sym) return;
      const next = loadWatchlistItems().filter((x) => x.ticker !== sym);
      saveWatchlistItems(next);
      syncSearchResultStars();
      void renderWatchlist();
    };

    const fetchWatchlistCardData = async (entry) => {
      const sym = entry.ticker;
      let quote = {};
      let scored = {};
      let profile = {};
      try {
        const [qRes, sRes, pRes] = await Promise.all([
          fetch(`/api/investor/quote?symbol=${encodeURIComponent(sym)}`),
          fetch(`/api/investor/score?symbol=${encodeURIComponent(sym)}&interval=6M`),
          fetch(`/api/investor/profile?symbol=${encodeURIComponent(sym)}`),
        ]);
        if (qRes.ok) quote = await qRes.json();
        if (sRes.ok) scored = await sRes.json();
        if (pRes.ok) profile = await pRes.json();
      } catch {
        /* partial data ok */
      }
      const quoteFromScore = scored?.quote && typeof scored.quote === "object" ? scored.quote : {};
      const outlook =
        (typeof scored?.explanation === "string" && scored.explanation.trim()) ||
        (typeof scored?.why_ranked === "string" && scored.why_ranked.trim()) ||
        "Long-term outlook unavailable — select for full research.";
      return {
        ticker: sym,
        company_name:
          (typeof profile?.company_name === "string" && profile.company_name.trim()) ||
          (typeof quote?.company_name === "string" && quote.company_name.trim()) ||
          entry.company_name ||
          sym,
        price: quote?.price ?? quoteFromScore?.price,
        change_percent: quote?.change_percent ?? quoteFromScore?.change_percent,
        score: scored?.score,
        rating: scored?.rating,
        outlook,
        rating_variant: watchlistRatingVariant(scored?.rating),
      };
    };

    const buildWatchlistCard = (data, onSelect) => {
      const card = document.createElement("article");
      card.className = `investor-stock-card investor-watchlist-card investor-watchlist-card--${data.rating_variant || "neutral"}`;
      card.setAttribute("role", "listitem");
      card.tabIndex = 0;

      const top = document.createElement("div");
      top.className = "investor-stock-top";

      const identity = document.createElement("div");
      identity.className = "investor-watchlist-identity";
      const tickerEl = document.createElement("p");
      tickerEl.className = "investor-ticker";
      tickerEl.textContent = data.ticker;
      const companyEl = document.createElement("p");
      companyEl.className = "investor-company";
      companyEl.textContent = text(data.company_name);
      identity.appendChild(tickerEl);
      identity.appendChild(companyEl);

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "investor-watchlist-remove";
      removeBtn.setAttribute("aria-label", `Remove ${data.ticker} from watchlist`);
      removeBtn.textContent = "Remove";
      removeBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        removeFromWatchlist(data.ticker);
      });

      top.appendChild(identity);
      top.appendChild(removeBtn);

      const metrics = document.createElement("div");
      metrics.className = "investor-stock-metrics investor-watchlist-metrics";

      const priceN = investorPresentNumber(data.price);
      const priceRow = document.createElement("p");
      priceRow.innerHTML = `Price <span>${priceN !== null ? formatUsdPriceInvestor(priceN) : "—"}</span>`;

      const chgTone = deltaToneClass(data.change_percent);
      const chgRow = document.createElement("p");
      chgRow.innerHTML = `Daily change <span class="investor-watchlist-delta investor-watchlist-delta--${chgTone}">${formatInvestorSignedPct(
        data.change_percent
      )}</span>`;

      const scoreRow = document.createElement("p");
      const scoreNum = Number(data.score);
      scoreRow.innerHTML = `${INVESTOR_OUTLOOK_TITLE} score <span>${Number.isFinite(scoreNum) ? `${scoreNum}/100` : "—"}</span>`;

      const ratingBadge = document.createElement("span");
      ratingBadge.className = `investor-watchlist-rating investor-watchlist-rating--${data.rating_variant || "neutral"}`;
      ratingBadge.textContent = normalizeInvestorRating(data.rating);

      metrics.appendChild(priceRow);
      metrics.appendChild(chgRow);
      metrics.appendChild(scoreRow);

      const ratingRow = document.createElement("div");
      ratingRow.className = "investor-watchlist-rating-row";
      ratingRow.appendChild(ratingBadge);

      const outlookLabel = document.createElement("p");
      outlookLabel.className = "investor-watchlist-outlook-label";
      outlookLabel.textContent = INVESTOR_OUTLOOK_TITLE;

      const outlookEl = document.createElement("p");
      outlookEl.className = "investor-watchlist-outlook";
      outlookEl.textContent = truncateOutlook(data.outlook, 140);

      const openCard = () => {
        onSelect({
          ticker: data.ticker,
          company_name: data.company_name,
        });
      };
      card.addEventListener("click", openCard);
      card.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          openCard();
        }
      });

      card.appendChild(top);
      card.appendChild(metrics);
      card.appendChild(ratingRow);
      card.appendChild(outlookLabel);
      card.appendChild(outlookEl);
      return card;
    };

    const renderWatchlist = async () => {
      const items = loadWatchlistItems();
      if (watchlistEmpty) watchlistEmpty.hidden = items.length > 0;
      if (!watchlistGrid) return;
      if (!items.length) {
        watchlistGrid.hidden = true;
        watchlistGrid.innerHTML = "";
        return;
      }
      watchlistGrid.hidden = false;
      watchlistGrid.innerHTML = "";
      items.forEach((entry) => {
        const skeleton = document.createElement("article");
        skeleton.className = "investor-stock-card investor-watchlist-card investor-watchlist-card--loading";
        skeleton.setAttribute("role", "listitem");
        skeleton.innerHTML = `<p class="investor-ticker">${escapeHtml(entry.ticker)}</p><p class="investor-watchlist-loading">Loading…</p>`;
        watchlistGrid.appendChild(skeleton);
      });

      const rows = await Promise.all(items.map((entry) => fetchWatchlistCardData(entry)));
      watchlistGrid.innerHTML = "";
      rows.forEach((data) => {
        watchlistGrid.appendChild(
          buildWatchlistCard(data, (item) => {
            setSelected(item, { fromSearch: false });
            const panel = document.getElementById("selected-stock-title");
            if (panel && typeof panel.scrollIntoView === "function") {
              panel.scrollIntoView({ behavior: "smooth", block: "start" });
            }
          })
        );
      });
    };

    const pointClose = (p) => {
      if (!p || typeof p !== "object") return NaN;
      const raw = p.close !== undefined && p.close !== null ? p.close : p.price;
      const n = Number(raw);
      return Number.isFinite(n) ? n : NaN;
    };

    const setChartDebugDetail = (detail) => {
      if (!chartDebugEl) return;
      const show = isInvestorChartDebug() && detail;
      chartDebugEl.hidden = !show;
      chartDebugEl.textContent = show ? String(detail) : "";
    };

    const showChartOverlay = (message, variant) => {
      if (chartWrap) {
        if (variant === "loading") chartWrap.setAttribute("aria-busy", "true");
        else chartWrap.removeAttribute("aria-busy");
      }
      clearInvestorSvgLayers();
      if (!chartEmpty) return;
      chartEmpty.hidden = false;
      chartEmpty.classList.remove("investor-chart-empty--error", "investor-chart-empty--loading", "investor-chart-empty--info");
      if (variant === "loading") chartEmpty.classList.add("investor-chart-empty--loading");
      else if (variant === "info" || variant === "error") chartEmpty.classList.add("investor-chart-empty--info");
      chartEmpty.textContent = variant === "loading" ? "" : message || "";
      if (variant === "loading") loadingQuoteStrip();
      else neutralQuoteStrip();
    };

    const svgEl = (name, attrs) => {
      const el = document.createElementNS(SVG_NS, name);
      if (attrs) {
        Object.keys(attrs).forEach((k) => el.setAttribute(k, attrs[k]));
      }
      return el;
    };

    const mapInvestorChartPoint = (pt, layout) => {
      const x = layout.padL + ((pt.t - layout.tMin) / layout.tSpan) * layout.usableW;
      const y = layout.padT + (1 - (pt.y - layout.yLow) / layout.ySpan) * layout.usableH;
      return { x, y };
    };

    const showChartHoverAt = (pt, layout, trend) => {
      if (!pt || !layout) return;
      const { x, y } = mapInvestorChartPoint(pt, layout);
      if (chartCrosshair) {
        chartCrosshair.setAttribute("x1", String(x));
        chartCrosshair.setAttribute("x2", String(x));
        chartCrosshair.setAttribute("y1", String(layout.padT));
        chartCrosshair.setAttribute("y2", String(layout.padT + layout.usableH));
        chartCrosshair.setAttribute("visibility", "visible");
      }
      if (chartHoverDot) {
        chartHoverDot.setAttribute("cx", String(x));
        chartHoverDot.setAttribute("cy", String(y));
        chartHoverDot.setAttribute(
          "class",
          `investor-chart-hover-dot investor-chart-hover-dot--${trend === "up" || trend === "down" ? trend : "neutral"}`
        );
        chartHoverDot.setAttribute("visibility", "visible");
      }
      if (chartTooltip && chartTooltipPrice && chartTooltipDate && chartWrap) {
        chartTooltipPrice.textContent = formatUsdPriceInvestor(pt.y);
        chartTooltipDate.textContent = formatInvestorTickTime(pt.t, currentRange);
        chartTooltip.hidden = false;
        const wrapRect = chartWrap.getBoundingClientRect();
        const svgRect = chartSvg ? chartSvg.getBoundingClientRect() : wrapRect;
        const relX = ((x / INVESTOR_CHART_W) * svgRect.width + (svgRect.left - wrapRect.left));
        const relY = ((y / INVESTOR_CHART_H) * svgRect.height + (svgRect.top - wrapRect.top));
        chartTooltip.style.left = `${Math.min(wrapRect.width - 8, Math.max(8, relX))}px`;
        chartTooltip.style.top = `${Math.max(8, relY)}px`;
      }
      const firstPx = layout.firstPx;
      const periodPct =
        Number.isFinite(firstPx) && firstPx > 0 ? ((pt.y - firstPx) / firstPx) * 100 : null;
      setInvestorQuoteStripFromRange(pt.y, pt.y - firstPx, periodPct, trend);
    };

    const wireInvestorChartHover = () => {
      if (!chartWrap || chartWrap.dataset.investorHoverWired === "1") return;
      chartWrap.dataset.investorHoverWired = "1";
      chartWrap.addEventListener("mousemove", (ev) => {
        const state = chartPlotState;
        if (!state || !state.series || state.series.length < 2 || !chartSvg) return;
        const rect = chartSvg.getBoundingClientRect();
        if (!rect.width) return;
        const relX = ((ev.clientX - rect.left) / rect.width) * INVESTOR_CHART_W;
        let bestIdx = 0;
        let bestDist = Infinity;
        for (let i = 0; i < state.series.length; i++) {
          const mapped = mapInvestorChartPoint(state.series[i], state.layout);
          const d = Math.abs(mapped.x - relX);
          if (d < bestDist) {
            bestDist = d;
            bestIdx = i;
          }
        }
        showChartHoverAt(state.series[bestIdx], state.layout, state.trend);
      });
      chartWrap.addEventListener("mouseleave", () => {
        hideChartHover();
        const state = chartPlotState;
        if (!state) return;
        setInvestorQuoteStripFromRange(state.lastPx, state.deltaDollar, state.periodPct, state.trend);
      });
    };

    const animateInvestorChartLine = () => {
      if (!chartLine) return;
      let lineLen = 0;
      try {
        lineLen = chartLine.getTotalLength();
      } catch {
        lineLen = 0;
      }
      if (!Number.isFinite(lineLen) || lineLen <= 0) return;
      chartLine.style.setProperty("--investor-line-len", String(lineLen));
      chartLine.setAttribute("stroke-dasharray", String(lineLen));
      chartLine.style.strokeDashoffset = String(lineLen);
      chartLine.classList.remove("investor-selected-chart-line--draw");
      void chartLine.getBoundingClientRect();
      chartLine.classList.add("investor-selected-chart-line--draw");
    };

    const renderSelectedChart = (points) => {
      if (!chartEmpty) return;
      if (!chartLine || !chartArea || !chartGrid || !chartYLabels || !chartXLabels || !chartClipRect) {
        showChartOverlay("Chart layers are missing from the page.", "error");
        return;
      }
      if (chartWrap) chartWrap.removeAttribute("aria-busy");
      clearInvestorSvgLayers();

      syncChartRangeLabel();
      const seriesRaw = [];
      for (let i = 0; i < (points?.length ?? 0); i++) {
        const p = points[i];
        const ts = pointUnixTime(p);
        const y = pointClose(p);
        if (!Number.isFinite(ts) || !Number.isFinite(y)) continue;
        seriesRaw.push({ t: ts, y });
      }
      seriesRaw.sort((a, b) => a.t - b.t);
      const series = [];
      for (let i = 0; i < seriesRaw.length; i++) {
        const row = seriesRaw[i];
        if (series.length && row.t === series[series.length - 1].t) {
          series[series.length - 1] = row;
        } else {
          series.push(row);
        }
      }
      if (series.length < 2) {
        showChartOverlay("Not enough closing prices to draw this range.", "");
        neutralQuoteStrip();
        return;
      }

      const W = INVESTOR_CHART_W;
      const H = INVESTOR_CHART_H;
      const padL = 56;
      const padR = 14;
      const padT = 14;
      const padB = 42;
      const usableW = W - padL - padR;
      const usableH = H - padT - padB;

      chartClipRect.setAttribute("x", String(padL));
      chartClipRect.setAttribute("y", String(padT));
      chartClipRect.setAttribute("width", String(usableW));
      chartClipRect.setAttribute("height", String(usableH));
      chartGrid.setAttribute("clip-path", "url(#investor-chart-clip)");

      const ys = series.map((p) => p.y);
      let yLow = Math.min(...ys);
      let yHigh = Math.max(...ys);
      const spread = Math.max(yHigh - yLow, Math.max(Math.abs(yHigh), 1) * 1e-9);
      yLow -= spread * 0.06;
      yHigh += spread * 0.06;
      const ySpan = Math.max(yHigh - yLow, 1e-12);

      const tMin = series[0].t;
      const tMax = series[series.length - 1].t;
      const tSpan = Math.max(tMax - tMin, 60);

      const layout = {
        W,
        H,
        padL,
        padR,
        padT,
        padB,
        usableW,
        usableH,
        yLow,
        yHigh,
        ySpan,
        tMin,
        tMax,
        tSpan,
        firstPx: series[0].y,
      };

      const fmtYtick = (v) => {
        const mx = Math.max(Math.abs(yHigh), Math.abs(yLow));
        if (mx >= 2500) return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
        if (mx >= 200) return v.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
        return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      };

      const yTicks = [];
      const yDivs = 4;
      for (let i = 0; i <= yDivs; i++) {
        yTicks.push(yHigh - ((yHigh - yLow) * i) / yDivs);
      }

      chartGrid.appendChild(
        svgEl("line", {
          class: "investor-chart-grid-axis",
          x1: String(padL),
          x2: String(W - padR),
          y1: String(padT + usableH),
          y2: String(padT + usableH),
        })
      );
      chartGrid.appendChild(
        svgEl("line", {
          class: "investor-chart-grid-axis",
          x1: String(padL),
          x2: String(padL),
          y1: String(padT),
          y2: String(padT + usableH),
        })
      );

      yTicks.forEach((yv) => {
        const fy = padT + (1 - (yv - yLow) / ySpan) * usableH;
        chartGrid.appendChild(
          svgEl("line", {
            class: "investor-chart-grid-line",
            x1: String(padL),
            x2: String(W - padR),
            y1: String(fy),
            y2: String(fy),
          })
        );
        const lab = svgEl("text", {
          x: String(padL - 6),
          y: String(fy + 3),
          "text-anchor": "end",
          "dominant-baseline": "middle",
        });
        lab.textContent = fmtYtick(yv);
        chartYLabels.appendChild(lab);
      });

      const n = series.length;
      const xTickSlots = Math.min(5, n);
      for (let k = 0; k < xTickSlots; k++) {
        const idx = xTickSlots === 1 ? 0 : Math.round((k / (xTickSlots - 1)) * (n - 1));
        const pt = series[idx];
        const fx = padL + ((pt.t - tMin) / tSpan) * usableW;
        const lx = svgEl("text", {
          x: String(Math.min(W - padR - 2, Math.max(padL + 24, fx))),
          y: String(H - 12),
          "text-anchor": "middle",
        });
        lx.textContent = formatInvestorTickTime(pt.t, currentRange);
        chartXLabels.appendChild(lx);
      }

      const coords = series.map((pt) => mapInvestorChartPoint(pt, layout));
      const pointsAttr = coords.map((c) => `${c.x.toFixed(2)},${c.y.toFixed(2)}`).join(" ");
      const baseY = padT + usableH;
      const areaD =
        `M ${coords[0].x.toFixed(2)} ${baseY.toFixed(2)} ` +
        coords.map((c) => `L ${c.x.toFixed(2)} ${c.y.toFixed(2)}`).join(" ") +
        ` L ${coords[coords.length - 1].x.toFixed(2)} ${baseY.toFixed(2)} Z`;

      const firstPx = series[0].y;
      const lastPx = series[series.length - 1].y;
      const periodPct =
        Number.isFinite(firstPx) && firstPx > 0 ? ((lastPx - firstPx) / firstPx) * 100 : null;
      const deltaDollar = lastPx - firstPx;
      const strokeTrend = trendStrokeClassFromPct(periodPct);

      chartArea.setAttribute("class", `investor-selected-chart-area investor-selected-chart-area--${strokeTrend}`);
      chartArea.setAttribute("d", areaD);
      chartLine.setAttribute("class", `investor-selected-chart-line investor-selected-chart-line--${strokeTrend}`);
      chartLine.setAttribute("points", pointsAttr);
      animateInvestorChartLine();
      setInvestorQuoteStripFromRange(lastPx, deltaDollar, periodPct, strokeTrend);

      chartPlotState = {
        series,
        layout: { ...layout, firstPx },
        lastPx,
        deltaDollar,
        periodPct,
        trend: strokeTrend,
      };

      const aria = [
        `Closing-price line chart, range ${currentRange}.`,
        `Last close ${formatUsdPriceInvestor(lastPx)}.`,
        `Range change ${formatSignedRangeDollar(deltaDollar)} (${formatSignedPeriodPct(periodPct)}).`,
      ].join(" ");
      if (chartWrap) chartWrap.setAttribute("aria-label", aria);

      chartEmpty.hidden = true;
      chartEmpty.classList.remove("investor-chart-empty--error", "investor-chart-empty--loading", "investor-chart-empty--info");
    };

    const loadSelectedChart = async (symbol, range) => {
      if (!symbol) return;
      setChartDebugDetail("");
      showChartOverlay("", "loading");
      try {
        const res = await fetch(
          `/api/investor/timeseries?symbol=${encodeURIComponent(symbol)}&range=${encodeURIComponent(range)}`
        );
        let payload = null;
        try {
          payload = await res.json();
        } catch {
          payload = null;
        }
        if (!res.ok) {
          const httpMsg =
            payload && typeof payload.detail === "string"
              ? payload.detail
              : payload && typeof payload.message === "string"
                ? payload.message
                : `HTTP ${res.status}`;
          const debugLine =
            (payload && payload.debug && payload.debug.detail) ||
            payload?.detail ||
            httpMsg ||
            "Request failed.";
          setChartDebugDetail(isInvestorChartDebug() ? debugLine : "");
          showChartOverlay(friendlyInvestorChartError(httpMsg) || `HTTP ${res.status}`, "info");
          return;
        }
        if (payload?.error) {
          const msg =
            typeof payload.message === "string" && payload.message.trim()
              ? payload.message
              : "Closing-price history unavailable.";
          const debugLine =
            (payload.debug && payload.debug.detail) ||
            payload.debug_detail ||
            msg;
          setChartDebugDetail(isInvestorChartDebug() ? debugLine : "");
          showChartOverlay(friendlyInvestorChartError(msg), "info");
          return;
        }
        const raw = Array.isArray(payload?.points) ? payload.points : [];
        setChartDebugDetail(
          isInvestorChartDebug() && payload?.debug?.detail ? String(payload.debug.detail) : ""
        );
        renderSelectedChart(raw);
      } catch (e) {
        const detail = e && e.message ? String(e.message) : String(e);
        setChartDebugDetail(isInvestorChartDebug() ? detail : "");
        showChartOverlay(`Chart request failed (${detail}).`.slice(0, 460), "info");
      } finally {
        void refreshInvestorDiagnostics();
      }
    };

    const setNewsStatus = (message) => {
      if (!newsStatus) return;
      newsStatus.textContent = message || "";
    };

    const clearNews = () => {
      if (newsList) newsList.innerHTML = "";
    };

    const truncateNewsSnippet = (raw, maxLen) => {
      const s = String(raw ?? "").trim();
      if (!s) return "";
      if (s.length <= maxLen) return s;
      return `${s.slice(0, maxLen - 1).trim()}…`;
    };

    const newsPayloadErrorMessage = (payload, res) => {
      if (payload && typeof payload === "object") {
        if (typeof payload.message === "string" && payload.message.trim()) return payload.message.trim();
        const d = payload.detail;
        if (typeof d === "string" && d.trim()) return d.trim();
        if (Array.isArray(d)) {
          const joined = d
            .map((x) =>
              typeof x === "object" && x?.msg ? String(x.msg) : typeof x?.detail !== "undefined" ? String(x.detail) : ""
            )
            .filter(Boolean)
            .join(" ");
          if (joined.trim()) return joined.trim();
        }
      }
      return `News request failed (HTTP ${res.status}).`;
    };

    const investorNewsPublishedLabel = (raw) => {
      if (!raw && raw !== 0) return "Date unavailable";
      try {
        if (typeof raw === "number" && Number.isFinite(raw)) {
          const ms = raw > 2e11 ? raw : raw * 1000;
          return new Date(ms).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
        }
        if (typeof raw === "string" && raw.trim()) {
          return new Date(raw).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
        }
      } catch {
        /* ignore */
      }
      return "Date unavailable";
    };

    const renderNews = (items, limit) => {
      if (!newsList) return;
      newsList.innerHTML = "";
      if (!Array.isArray(items) || !items.length) return;
      const cap = typeof limit === "number" && limit > 0 ? limit : 3;
      items.slice(0, cap).forEach((item) => {
        if (!item || typeof item !== "object") return;
        if (typeof item.headline !== "string" || !item.headline.trim()) return;
        const row = document.createElement("article");
        row.className = "investor-news-item";
        row.setAttribute("role", "listitem");

        const urlStr = String(item.url || "").trim();
        const title =
          urlStr && urlStr !== "#"
            ? document.createElement("a")
            : document.createElement("span");
        title.className = urlStr && urlStr !== "#" ? "investor-news-headline" : "investor-news-headline investor-news-headline--text";
        title.textContent = text(item.headline);
        if (urlStr && urlStr !== "#") {
          title.href = urlStr;
          title.target = "_blank";
          title.rel = "noopener noreferrer";
        }

        const meta = document.createElement("p");
        meta.className = "investor-news-meta";
        const sourceEl = document.createElement("span");
        sourceEl.className = "investor-news-meta-source";
        sourceEl.textContent = text(item.source);
        const dateEl = document.createElement("span");
        dateEl.className = "investor-news-meta-date";
        dateEl.textContent = investorNewsPublishedLabel(item.published_at);
        meta.appendChild(sourceEl);
        meta.appendChild(dateEl);

        row.appendChild(title);
        row.appendChild(meta);

        const sumRaw = item.summary;
        const snippet = typeof sumRaw === "string" && sumRaw.trim() ? truncateNewsSnippet(sumRaw, 160) : "";
        if (snippet) {
          const summary = document.createElement("p");
          summary.className = "investor-news-summary";
          summary.textContent = snippet;
          row.appendChild(summary);
        }

        newsList.appendChild(row);
      });
    };

    const formatInsiderUsd = (v) => {
      const n = investorPresentNumber(v);
      if (n === null) return null;
      if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
      if (n >= 10_000) return `$${Math.round(n).toLocaleString()}`;
      return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
    };

    const setInsiderLoading = () => {
      if (insiderSummary) {
        insiderSummary.textContent = "Loading recent insider filings…";
        insiderSummary.className = "investor-insider-summary investor-insider-summary--neutral";
      }
      if (insiderList) insiderList.innerHTML = "";
    };

    const setInsiderEmpty = (message, tone) => {
      const t = tone === "bullish" || tone === "cautious" ? tone : "neutral";
      if (insiderSummary) {
        insiderSummary.textContent =
          message || "No recent insider activity found";
        insiderSummary.className = `investor-insider-summary investor-insider-summary--${t}`;
      }
      if (insiderList) insiderList.innerHTML = "";
    };

    const renderInsiderRows = (items, summaryText, tone) => {
      if (!insiderList) return;
      insiderList.innerHTML = "";
      const t = tone === "bullish" || tone === "cautious" ? tone : "neutral";
      if (insiderSummary) {
        insiderSummary.textContent = summaryText || "Recent insider activity appears neutral";
        insiderSummary.className = `investor-insider-summary investor-insider-summary--${t}`;
      }
      items.forEach((row) => {
        if (!row || typeof row !== "object") return;
        const el = document.createElement("div");
        el.className = "investor-insider-row";
        el.setAttribute("role", "listitem");
        const side = String(row.side || "Other");
        const sideClass =
          side === "Buy" ? "buy" : side === "Sell" ? "sell" : "other";
        const shares =
          row.shares !== null && row.shares !== undefined && Number.isFinite(Number(row.shares))
            ? Number(row.shares).toLocaleString()
            : "—";
        const value = formatInsiderUsd(row.transaction_value_usd) || "—";
        const role = row.role_title ? String(row.role_title) : "—";
        const date = row.transaction_date || row.filing_date || "—";
        el.innerHTML = `
          <div class="investor-insider-row-top">
            <span class="investor-insider-side investor-insider-side--${sideClass}">${escapeHtml(side)}</span>
            <span class="investor-insider-name">${escapeHtml(String(row.insider_name || "—"))}</span>
            <span class="investor-insider-date">${escapeHtml(String(date))}</span>
          </div>
          <div class="investor-insider-row-meta">
            <span class="investor-insider-meta">${escapeHtml(role)}</span>
            <span class="investor-insider-meta">${escapeHtml(shares)} sh</span>
            <span class="investor-insider-meta">${escapeHtml(value)}</span>
          </div>
        `;
        insiderList.appendChild(el);
      });
    };

    const loadInsiderActivity = async (symbol) => {
      if (!symbol) return;
      setInsiderLoading();
      try {
        const res = await fetch(`/api/investor/insiders?symbol=${encodeURIComponent(symbol)}`);
        let payload = null;
        try {
          payload = await res.json();
        } catch {
          payload = null;
        }
        if (!res.ok || !payload) {
          setInsiderEmpty("No recent insider activity found", "neutral");
          return;
        }
        const items = Array.isArray(payload.items) ? payload.items : [];
        const tone = payload.summary_tone || "neutral";
        if (!items.length || payload.empty) {
          setInsiderEmpty(payload.message || "No recent insider activity found", tone);
          return;
        }
        renderInsiderRows(
          items,
          payload.summary_text || "Recent insider activity appears neutral",
          tone
        );
      } catch {
        setInsiderEmpty("No recent insider activity found", "neutral");
      }
    };

    const loadNews = async (symbol) => {
      if (!symbol) return;
      setNewsStatus("Loading supporting headlines…");
      clearNews();
      try {
        const res = await fetch(
          `/api/investor/news?symbol=${encodeURIComponent(symbol)}&limit=${encodeURIComponent(String(12))}`
        );
        const resText = await res.text();
        let payload = null;
        try {
          payload = resText ? JSON.parse(resText) : null;
        } catch {
          payload = null;
        }
        if (!res.ok) {
          setNewsStatus(newsPayloadErrorMessage(payload, res));
          return;
        }
        if (!payload || payload.error) {
          const m =
            payload && typeof payload.message === "string" && payload.message.trim()
              ? payload.message.trim()
              : "Could not load news.";
          setNewsStatus(m);
          return;
        }
        const items = Array.isArray(payload?.items) ? payload.items : [];
        if (!items.length) {
          setNewsStatus("No recent headlines — thesis still reflects price and score pillars.");
          return;
        }
        const shown = Math.min(3, items.length);
        setNewsStatus(`${shown} headline${shown === 1 ? "" : "s"} supporting the thesis`);
        renderNews(items, 3);
      } catch {
        setNewsStatus("Could not load news.");
        clearNews();
      } finally {
        void refreshInvestorDiagnostics();
      }
    };

    const setScoreLoading = () => {
      if (selectedScore) selectedScore.textContent = "…";
      if (selectedRating) {
        selectedRating.textContent = "…";
        selectedRating.className = "investor-rating-badge investor-rating-badge--neutral";
      }
      if (selectedBreakdown) {
        selectedBreakdown.innerHTML = "";
        const row = document.createElement("div");
        row.className = "investor-pillar-row investor-pillar-row--loading";
        row.textContent = "Loading research pillars…";
        selectedBreakdown.appendChild(row);
      }
      if (selectedExplanation) {
        selectedExplanation.textContent = "Building long-term research view…";
      }
      if (selectedRisk) selectedRisk.textContent = "Assessing long-term risk factors…";
    };

    const setScoreError = () => {
      if (selectedScore) selectedScore.textContent = "—";
      if (selectedRating) {
        selectedRating.textContent = "—";
        selectedRating.className = "investor-rating-badge investor-rating-badge--neutral";
      }
      if (selectedBreakdown) renderInvestorPillarBreakdown(selectedBreakdown, {}, null);
      if (selectedExplanation) {
        selectedExplanation.textContent =
          "Research score unavailable — keep this name on watch and retry when data reconnects.";
      }
      if (selectedRisk) {
        selectedRisk.textContent = `Long-term thesis can shift with earnings, macro, or sentiment. ${INVESTOR_RESEARCH_DISCLAIMER}`;
      }
    };

    const loadScore = async (symbol) => {
      if (!symbol) return;
      setScoreLoading();
      try {
        const res = await fetch(
          `/api/investor/score?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(currentRange)}`
        );
        if (!res.ok) {
          setScoreError();
          return;
        }
        const payload = await res.json();
        if (selectedScore) selectedScore.textContent = `${text(payload?.score)}/100`;
        applyInvestorRatingBadge(selectedRating, payload?.rating);
        renderInvestorPillarBreakdown(
          selectedBreakdown,
          payload?.breakdown || {},
          payload?.score_pillars
        );
        if (selectedExplanation) {
          const why = typeof payload?.why_ranked === "string" ? payload.why_ranked.trim() : "";
          selectedExplanation.textContent = why || text(payload?.explanation);
        }
        if (selectedRisk) {
          selectedRisk.textContent =
            text(payload?.risk_warning) ||
            `Long-term risk factors under review. ${INVESTOR_RESEARCH_DISCLAIMER}`;
        }
      } catch {
        setScoreError();
      }
    };

    const loadResearchSummary = async (symbol) => {
      if (!symbol) return;
      setThesisLoading();
      try {
        const res = await fetch(
          `/api/investor/research-summary?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(currentRange)}`
        );
        if (!res.ok) {
          setThesisError();
          return;
        }
        const payload = await res.json();
        applyThesisPayload(payload);
      } catch {
        setThesisError();
      }
    };

    const opportunitySparkRowHtml = (item) => {
      const pts = Array.isArray(item?.sparkline_points) ? item.sparkline_points : [];
      const vals = pts
        .map((p) => {
          if (!p || typeof p !== "object") return NaN;
          const raw = p.close !== undefined && p.close !== null ? p.close : p.price;
          const n = Number(raw);
          return Number.isFinite(n) ? n : NaN;
        })
        .filter((v) => Number.isFinite(v));
      if (vals.length < 2) return "";
      let pct =
        typeof item?.period_return_percent === "number" && Number.isFinite(item.period_return_percent)
          ? item.period_return_percent
          : periodReturnPercentFromVals(vals);
      const trend = trendStrokeClassFromPct(pct);
      const pctText =
        pct === null || pct === undefined || !Number.isFinite(Number(pct))
          ? "—"
          : formatSignedPeriodPct(pct);
      const polyClass = `investor-opp-spark-line investor-opp-spark-line--${trend}`;
      const intervalHint = opportunitiesIntervalLabel || "Period";
      const attr = buildSparkPolylineAttr(vals, 120, 36, 2, 3);
      const svgInner = `<svg class="investor-opp-spark-svg" viewBox="0 0 120 36" preserveAspectRatio="none" aria-hidden="true"><polyline class="${polyClass}" points="${attr}" /></svg>`;
      const escHint = escapeHtml(intervalHint);
      return `
        <div class="investor-opp-spark-row">
          <div class="investor-opp-spark-wrap">${svgInner}</div>
          <span class="investor-opp-spark-pct investor-opp-spark-pct--${trend}" title="${escHint} total return">${escapeHtml(
            pctText
          )}</span>
        </div>
      `;
    };

    const ratingBadgeVariant = (item) => investorRatingVariant(item?.rating_badge || item?.rating);

    const deltaToneFromPct = (pct) => {
      if (pct === null || pct === undefined || !Number.isFinite(Number(pct))) return "muted";
      const n = Number(pct);
      if (Math.abs(n) < 0.02) return "muted";
      return n >= 0 ? "up" : "down";
    };

    const buildOpportunityCard = (item) => {
      const card = document.createElement("button");
      card.type = "button";
      const variant = ratingBadgeVariant(item);
      card.className = `investor-card investor-opp-card investor-opp-card--${variant}`;
      card.setAttribute("role", "listitem");

      const dash = "—";
      const trimStr = (v) => (typeof v === "string" ? v.trim() : "");
      const rawRating = trimStr(item?.rating_badge) || trimStr(item?.rating);
      const ratingText = rawRating ? normalizeInvestorRating(rawRating) : dash;
      const scoreNum = Number(item?.score);
      const scoreText = Number.isFinite(scoreNum) ? String(scoreNum) : dash;
      const companyText = trimStr(item?.company_name) || dash;

      const formatUsd = (v) => {
        const n = investorPresentNumber(v);
        if (n === null) return null;
        const frac = Math.abs(n) >= 1 ? 2 : 4;
        return `$${n.toLocaleString(undefined, { minimumFractionDigits: frac, maximumFractionDigits: frac })}`;
      };

      const formatSignedUsd = (v) => {
        const n = investorPresentNumber(v);
        if (n === null) return null;
        const sign = n >= 0 ? "+" : "−";
        const abs = Math.abs(n);
        const frac = abs >= 1 ? 2 : 4;
        return `${sign}$${abs.toLocaleString(undefined, { minimumFractionDigits: frac, maximumFractionDigits: frac })}`;
      };

      const formatSignedPct = (v) => {
        const n = investorPresentNumber(v);
        if (n === null) return null;
        const sign = n >= 0 ? "+" : "−";
        return `${sign}${Math.abs(n).toFixed(2)}%`;
      };

      const head = document.createElement("header");
      head.className = "investor-opp-head";

      const row1 = document.createElement("div");
      row1.className = "investor-opp-topline";

      const tick = document.createElement("span");
      tick.className = "investor-opp-ticker";
      tick.textContent = text(item?.ticker);

      const badge = document.createElement("span");
      badge.className = `investor-opp-badge investor-opp-badge--${variant}`;
      badge.textContent = ratingText;

      const scorePill = document.createElement("span");
      scorePill.className = "investor-opp-scorepill";
      const scoreLab = document.createElement("span");
      scoreLab.className = "investor-opp-scorepill-lab";
      scoreLab.textContent = "Score";
      const scoreVal = document.createElement("span");
      scoreVal.className = "investor-opp-scorepill-val";
      scoreVal.textContent = scoreText;
      scorePill.appendChild(scoreLab);
      scorePill.appendChild(scoreVal);

      row1.appendChild(tick);
      row1.appendChild(badge);
      row1.appendChild(scorePill);

      const company = document.createElement("p");
      company.className = "investor-opp-company";
      company.textContent = companyText;

      head.appendChild(row1);
      head.appendChild(company);

      const priceWrap = document.createElement("div");
      priceWrap.className = "investor-opp-priceblock";
      const last = document.createElement("div");
      last.className = "investor-opp-last";
      const lastLabel = document.createElement("span");
      lastLabel.className = "investor-opp-last-label";
      lastLabel.textContent = "Last";
      const lastVal = document.createElement("span");
      lastVal.className = "investor-opp-last-val";
      lastVal.textContent =
        trimStr(item?.current_price_display) || formatUsd(item?.current_price) || dash;
      last.appendChild(lastLabel);
      last.appendChild(lastVal);

      const dt = deltaToneFromPct(item?.daily_change_percent);
      const deltas = document.createElement("div");
      deltas.className = "investor-opp-deltas";

      const usd = document.createElement("span");
      usd.className = `investor-opp-delta investor-opp-delta--${dt}`;
      usd.textContent =
        trimStr(item?.daily_change_dollars_display) || formatSignedUsd(item?.daily_change_dollar) || dash;

      const pctp = document.createElement("span");
      pctp.className = `investor-opp-delta investor-opp-delta--${dt}`;
      pctp.textContent =
        trimStr(item?.daily_change_percent_display) || formatSignedPct(item?.daily_change_percent) || dash;

      deltas.appendChild(usd);
      deltas.appendChild(pctp);
      priceWrap.appendChild(last);
      priceWrap.appendChild(deltas);

      const sparkHtml = opportunitySparkRowHtml(item);

      const thesis = document.createElement("div");
      thesis.className = "investor-opp-outlook-block";
      const thesisLabel = document.createElement("p");
      thesisLabel.className = "investor-opp-outlook-label";
      thesisLabel.textContent = INVESTOR_OUTLOOK_TITLE;
      const thesisBody = document.createElement("p");
      thesisBody.className = "investor-opp-thesis";
      const thesisText = typeof item?.reason_short === "string" ? item.reason_short.trim() : "";
      if (thesisText) {
        thesisBody.textContent = thesisText;
      } else {
        thesisBody.hidden = true;
      }
      thesis.appendChild(thesisLabel);
      thesis.appendChild(thesisBody);

      const tierLine = document.createElement("p");
      tierLine.className = "investor-opp-tier-hint";
      const tierTxt = typeof item?.tier_hint === "string" ? item.tier_hint.trim() : "";
      if (tierTxt) {
        tierLine.textContent = tierTxt;
      } else {
        tierLine.hidden = true;
      }

      const newsHead = document.createElement("div");
      newsHead.className = "investor-opp-news-head";
      newsHead.textContent = "Recent headlines";

      const newsUl = document.createElement("ul");
      newsUl.className = "investor-opp-news";
      const newsItems = Array.isArray(item?.news_items) ? item.news_items.slice(0, 2) : [];
      if (item?.news_error) {
        const nm =
          typeof item?.news_message === "string" && item.news_message.trim()
            ? item.news_message.trim()
            : "Could not load news.";
        const li = document.createElement("li");
        li.className = "investor-opp-news-line investor-opp-news-line--error";
        li.textContent = nm;
        newsUl.appendChild(li);
      } else if (newsItems.length > 0) {
        newsItems.forEach((n) => {
          const li = document.createElement("li");
          li.className = "investor-opp-news-line";
          const dtStr = investorNewsPublishedLabel(n?.published_at);
          const src = text(n?.source);
          const meta = document.createElement("span");
          meta.className = "investor-opp-news-meta";
          meta.textContent = [src, dtStr].filter(Boolean).join(" · ");
          const url = String(n?.url || "").trim();
          if (isLikelyHttpUrl(url)) {
            const a = document.createElement("a");
            a.className = "investor-opp-news-link";
            a.href = url;
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            a.textContent = text(n?.headline);
            a.addEventListener("click", (ev) => ev.stopPropagation());
            li.appendChild(a);
          } else {
            const span = document.createElement("span");
            span.className = "investor-opp-news-text";
            span.textContent = text(n?.headline);
            li.appendChild(span);
          }
          li.appendChild(meta);
          const sumRaw = typeof n?.summary === "string" ? n.summary.trim() : "";
          if (sumRaw) {
            const sp = document.createElement("p");
            sp.className = "investor-opp-news-summary";
            sp.textContent = truncateNewsSnippet(sumRaw, 200);
            li.appendChild(sp);
          }
          if (isLikelyHttpUrl(url)) {
            const ua = document.createElement("a");
            ua.className = "investor-opp-news-raw-url";
            ua.href = url;
            ua.target = "_blank";
            ua.rel = "noopener noreferrer";
            ua.textContent = url.length > 72 ? `${url.slice(0, 70)}…` : url;
            ua.addEventListener("click", (ev) => ev.stopPropagation());
            li.appendChild(ua);
          }
          newsUl.appendChild(li);
        });
      } else {
        const li = document.createElement("li");
        li.className = "investor-opp-news-line investor-opp-news-line--empty";
        li.textContent = "No recent news found.";
        newsUl.appendChild(li);
      }

      const metrics = document.createElement("footer");
      metrics.className = "investor-opp-metrics";
      const km = Array.isArray(item?.key_metrics) ? item.key_metrics : [];
      if (km.length) {
        km.forEach((m) => {
          if (!m || typeof m !== "object") return;
          const lab = String(m.label || "").trim();
          const val = String(m.value || "").trim();
          if (!lab || !val) return;
          const chip = document.createElement("div");
          chip.className = "investor-opp-metric-chip";
          const lb = document.createElement("span");
          lb.className = "investor-opp-metric-lab";
          lb.textContent = lab;
          const vl = document.createElement("span");
          vl.className = "investor-opp-metric-val";
          vl.textContent = val;
          chip.appendChild(lb);
          chip.appendChild(vl);
          metrics.appendChild(chip);
        });
      } else {
        metrics.hidden = true;
      }

      card.appendChild(head);
      card.appendChild(priceWrap);
      if (sparkHtml) {
        const sparkHost = document.createElement("div");
        sparkHost.className = "investor-opp-spark-host";
        sparkHost.innerHTML = sparkHtml;
        card.appendChild(sparkHost);
      }
      card.appendChild(thesis);
      card.appendChild(tierLine);
      card.appendChild(newsHead);
      card.appendChild(newsUl);
      card.appendChild(metrics);

      card.addEventListener("click", () => {
        setSelected(
          {
            ticker: item?.ticker,
            company_name: item?.company_name,
            exchange: item?.exchange,
            asset_type: item?.asset_type,
          },
          { fromSearch: false }
        );
        const selectedPanel = document.getElementById("selected-stock-title");
        if (selectedPanel && typeof selectedPanel.scrollIntoView === "function") {
          selectedPanel.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
      return card;
    };

    const renderOpportunities = (items) => {
      if (!oppsList) return;
      oppsList.innerHTML = "";
      if (!Array.isArray(items) || !items.length) return;
      items.forEach((item) => {
        oppsList.appendChild(buildOpportunityCard(item));
      });
    };

    const loadOpportunities = async () => {
      if (oppsStatus) oppsStatus.textContent = "Ranking watchlist candidates for long-term research…";
      if (oppsList) oppsList.innerHTML = "";
      const oppsTitle = document.getElementById("investor-opportunities-title");
      try {
        const res = await fetch("/api/investor/opportunities?interval=6M");
        if (!res.ok) {
          if (oppsStatus) oppsStatus.textContent = "Unable to load watchlist rankings right now.";
          void refreshInvestorDiagnostics();
          return;
        }
        const payload = await res.json();
        const items = Array.isArray(payload?.items) ? payload.items : [];
        opportunitiesIntervalLabel = typeof payload?.interval === "string" ? payload.interval : "6M";
        if (oppsTitle && payload?.list_heading) {
          oppsTitle.textContent = payload.list_heading;
        }
        if (!items.length) {
          if (oppsStatus) oppsStatus.textContent = "No watchlist candidates available right now.";
          void refreshInvestorDiagnostics();
          return;
        }
        const sub = payload?.list_subheading || "Ranked for long-term investor research.";
        const disclaimer = payload?.research_disclaimer || "This is research support, not financial advice.";
        const dataNote = payload?.data_quality_note ? ` ${payload.data_quality_note}` : "";
        if (oppsStatus) {
          oppsStatus.textContent = `${sub}${dataNote} ${disclaimer}`;
        }
        renderOpportunities(items);
        void refreshInvestorDiagnostics();
      } catch {
        if (oppsStatus) oppsStatus.textContent = "Unable to load watchlist rankings right now.";
        if (oppsList) oppsList.innerHTML = "";
        void refreshInvestorDiagnostics();
      }
    };

    const renderResults = (items) => {
      if (!searchMounted || !results) return;
      results.innerHTML = "";
      if (!Array.isArray(items) || !items.length) return;
      items.forEach((item) => {
        const sym = String(item?.ticker || "")
          .trim()
          .toUpperCase();
        const row = document.createElement("div");
        row.className = "investor-search-result";
        row.setAttribute("role", "listitem");

        const body = document.createElement("button");
        body.type = "button";
        body.className = "investor-search-result-body";
        body.innerHTML = `
          <span class="investor-search-result-ticker">${text(item.ticker)}</span>
          <span class="investor-search-result-company">${text(item.company_name)}</span>
          <span class="investor-search-result-meta">${text(item.exchange)} • ${text(item.type || item.asset_type)}</span>
        `;
        body.addEventListener("click", () => setSelected(item, { fromSearch: true }));

        const starBtn = document.createElement("button");
        starBtn.type = "button";
        starBtn.className = "investor-watchlist-star";
        starBtn.dataset.ticker = sym;
        starBtn.innerHTML = watchlistStarSvg();
        if (isOnWatchlist(sym)) {
          starBtn.classList.add("investor-watchlist-star--active");
          starBtn.setAttribute("aria-pressed", "true");
          starBtn.setAttribute("aria-label", `Remove ${sym} from My Watchlist`);
        } else {
          starBtn.setAttribute("aria-pressed", "false");
          starBtn.setAttribute("aria-label", `Add ${sym} to My Watchlist`);
        }
        starBtn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          toggleWatchlistItem(item);
        });

        row.appendChild(body);
        row.appendChild(starBtn);
        results.appendChild(row);
      });
    };

    const runSearch = async () => {
      if (!searchMounted) return;
      const q = String(input.value || "").trim();
      if (!q) {
        setStatus("Type a ticker or company name.", true);
        results.innerHTML = "";
        return;
      }
      button.disabled = true;
      setStatus("Searching...", true);
      try {
        const res = await fetch(`/api/investor/search?q=${encodeURIComponent(q)}`);
        if (!res.ok) {
          setStatus("Search failed. Try again.", true);
          results.innerHTML = "";
          return;
        }
        const payload = await res.json();
        const items = Array.isArray(payload?.items) ? payload.items : [];
        renderResults(items);
        if (!items.length) {
          setStatus("No matches found.", true);
        } else {
          setStatus(`Found ${items.length} result${items.length === 1 ? "" : "s"}.`, true);
        }
      } catch {
        setStatus("Search failed. Try again.", true);
        results.innerHTML = "";
      } finally {
        button.disabled = false;
      }
    };

    if (searchMounted) {
      button.addEventListener("click", () => {
        void runSearch();
      });

      input.addEventListener("keydown", (ev) => {
        if (ev.key !== "Enter") return;
        ev.preventDefault();
        void runSearch();
      });
    }

    rangeButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        const range = String(btn.dataset.range || "1D").toUpperCase();
        currentRange = range;
        syncChartRangeLabel();
        rangeButtons.forEach((x) => x.classList.toggle("investor-range-btn--active", x === btn));
        if (selected?.ticker) {
          void loadSelectedChart(selected.ticker, currentRange);
          void loadScore(selected.ticker);
          void loadResearchSummary(selected.ticker);
        }
      });
    });

    void loadOpportunities();
    void renderWatchlist();
    document.addEventListener("ragx-active-tab", (ev) => {
      if ((ev.detail || {}).tab === "investor" && loadWatchlistItems().length > 0) {
        void renderWatchlist();
      }
    });
    wireInvestorChartHover();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      mountInvestorDiagnosticsRouting();
      mountInvestorSearch();
    });
  } else {
    mountInvestorDiagnosticsRouting();
    mountInvestorSearch();
  }
})();

