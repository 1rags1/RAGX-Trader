/**
 * Investor dashboard interactions (search + selected stock panel).
 * Live data: FINNHUB_API_KEY (quotes, profile, Finnhub candles) + optional TWELVE_DATA_API_KEY (candle fallback).
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

  function formatSignedPeriodPct(pct) {
    if (pct === null || !Number.isFinite(pct)) return "—";
    const sign = pct > 0 ? "+" : "";
    return `${sign}${pct.toFixed(2)}%`;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";

  /** Plain closing price · line chart — not OHLC candles */
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
    const regime = document.getElementById("investor-kpi-regime");
    const macro = document.getElementById("investor-kpi-macro");
    const panel = document.getElementById("investor-diagnostics-panel");
    if (!banner || !panel) return;

    const variant = j?.badge_variant === "live" ? "live" : "demo";
    banner.className = `investor-mode-banner investor-mode-banner--${variant}`;
    banner.textContent =
      j?.badge_label || "Demo Mode / Missing API Keys";

    if (sub) {
      sub.textContent = j?.fully_live
        ? "Live investor feeds are connected — scores, charts, and news use configured APIs."
        : "Demo mode or missing keys — add FINNHUB_API_KEY (+ optional TWELVE_DATA_API_KEY as candle fallback) and a news key for live mode.";
    }

    if (regime) {
      regime.textContent = j?.fully_live
        ? "Regime is not auto-labeled here — use Trader price action and your own process."
        : "Regime readouts stay off while market or news APIs are not fully live.";
    }

    if (macro) {
      macro.textContent = j?.fully_live
        ? "Macro is not auto-summarized — pair company news with your macro research stack."
        : "Macro overlays wait for full API coverage; news headline cards show feed status.";
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
    const summaryHappening = document.getElementById("investor-summary-happening");
    const summaryWhy = document.getElementById("investor-summary-why");
    const summaryHelp = document.getElementById("investor-summary-help");
    const summaryHurt = document.getElementById("investor-summary-hurt");
    const summaryConclusion = document.getElementById("investor-summary-conclusion");
    const summarySources = document.getElementById("investor-summary-sources");
    const oppsStatus = document.getElementById("investor-opps-status");
    const oppsList = document.getElementById("investor-opps-list");
    const rangeButtons = Array.from(document.querySelectorAll(".investor-range-btn[data-range]"));
    const chartLine = document.getElementById("investor-selected-chart-line");
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
    let currentRange = "1D";
    let opportunitiesIntervalLabel = "6M";

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

    const clearInvestorSvgLayers = () => {
      if (chartGrid) {
        chartGrid.innerHTML = "";
        chartGrid.removeAttribute("clip-path");
      }
      if (chartYLabels) chartYLabels.innerHTML = "";
      if (chartXLabels) chartXLabels.innerHTML = "";
      if (chartLine) {
        chartLine.setAttribute("points", "");
        chartLine.setAttribute("class", "investor-selected-chart-line investor-selected-chart-line--neutral");
      }
      if (chartClipRect) {
        chartClipRect.setAttribute("x", "0");
        chartClipRect.setAttribute("y", "0");
        chartClipRect.setAttribute("width", "640");
        chartClipRect.setAttribute("height", "248");
      }
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

    const setSelected = (item) => {
      selected = item || null;
      if (!selectedTicker || !selectedCompany || !selectedExchange || !selectedAssetType) return;
      selectedTicker.textContent = text(item?.ticker);
      selectedCompany.textContent = text(item?.company_name);
      selectedExchange.textContent = text(item?.exchange);
      selectedAssetType.textContent = text(item?.asset_type);
      if (item?.ticker) {
        void loadInvestorProfile(item.ticker);
        void loadSelectedChart(item.ticker, currentRange);
        void loadNews(item.ticker);
        void loadScore(item.ticker);
        void loadResearchSummary(item.ticker);
      }
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
      chartEmpty.classList.remove("investor-chart-empty--error", "investor-chart-empty--loading");
      if (variant === "loading") chartEmpty.classList.add("investor-chart-empty--loading");
      else if (variant === "error") chartEmpty.classList.add("investor-chart-empty--error");
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

    const renderSelectedChart = (points) => {
      if (!chartEmpty) return;
      if (!chartLine || !chartGrid || !chartYLabels || !chartXLabels || !chartClipRect) {
        showChartOverlay("Chart SVG layers are missing from the page.", "error");
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

      const W = 640;
      const H = 248;
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

      const xLine = svgEl("line", {
        class: "investor-chart-grid-axis",
        x1: String(padL),
        x2: String(W - padR),
        y1: String(padT + usableH),
        y2: String(padT + usableH),
      });
      chartGrid.appendChild(xLine);

      const yLine = svgEl("line", {
        class: "investor-chart-grid-axis",
        x1: String(padL),
        x2: String(padL),
        y1: String(padT),
        y2: String(padT + usableH),
      });
      chartGrid.appendChild(yLine);

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

      const pointsAttr = series
        .map((pt) => {
          const x = padL + ((pt.t - tMin) / tSpan) * usableW;
          const y = padT + (1 - (pt.y - yLow) / ySpan) * usableH;
          return `${x.toFixed(2)},${y.toFixed(2)}`;
        })
        .join(" ");

      const firstPx = series[0].y;
      const lastPx = series[series.length - 1].y;
      const periodPct =
        Number.isFinite(firstPx) && firstPx > 0 ? ((lastPx - firstPx) / firstPx) * 100 : null;
      const deltaDollar = lastPx - firstPx;
      const strokeTrend = trendStrokeClassFromPct(periodPct);

      chartLine.setAttribute(
        "class",
        `investor-selected-chart-line investor-selected-chart-line--${strokeTrend}`
      );
      chartLine.setAttribute("points", pointsAttr);
      setInvestorQuoteStripFromRange(lastPx, deltaDollar, periodPct, strokeTrend);

      const aria = [
        `Closing-price line chart, range ${currentRange}.`,
        `Last plotted close ${formatUsdPriceInvestor(lastPx)}.`,
        `Range change ${formatSignedRangeDollar(deltaDollar)} (${formatSignedPeriodPct(periodPct)}).`,
        "Not candles — single close line only.",
      ].join(" ");
      if (chartWrap) chartWrap.setAttribute("aria-label", aria);

      chartEmpty.hidden = true;
      chartEmpty.classList.remove("investor-chart-empty--error", "investor-chart-empty--loading");
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
          showChartOverlay((httpMsg && String(httpMsg).trim()) || `HTTP ${res.status}`, "error");
          return;
        }
        if (payload?.error) {
          const msg = typeof payload.message === "string" && payload.message.trim() ? payload.message : "Chart unavailable.";
          const debugLine =
            (payload.debug && payload.debug.detail) ||
            payload.debug_detail ||
            msg;
          setChartDebugDetail(isInvestorChartDebug() ? debugLine : "");
          showChartOverlay(msg, "error");
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
        showChartOverlay(`Chart request failed (${detail}).`.slice(0, 460), "error");
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

    const renderNews = (items) => {
      if (!newsList) return;
      newsList.innerHTML = "";
      if (!Array.isArray(items) || !items.length) return;
      items.forEach((item) => {
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
        const dateStr = investorNewsPublishedLabel(item.published_at);
        meta.textContent = `${text(item.source)} · ${dateStr}`;

        row.appendChild(title);
        row.appendChild(meta);

        const sumRaw = item.summary;
        if (typeof sumRaw === "string" && sumRaw.trim()) {
          const summary = document.createElement("p");
          summary.className = "investor-news-summary";
          summary.textContent = sumRaw.trim();
          row.appendChild(summary);
        }

        if (isLikelyHttpUrl(urlStr)) {
          const urlRow = document.createElement("p");
          urlRow.className = "investor-news-url-row";
          const lab = document.createElement("span");
          lab.className = "investor-news-url-lab";
          lab.textContent = "URL";
          urlRow.appendChild(lab);
          urlRow.appendChild(document.createTextNode(": "));
          const ua = document.createElement("a");
          ua.href = urlStr;
          ua.className = "investor-news-url";
          ua.target = "_blank";
          ua.rel = "noopener noreferrer";
          ua.textContent = urlStr;
          urlRow.appendChild(ua);
          row.appendChild(urlRow);
        }

        newsList.appendChild(row);
      });
    };

    const loadNews = async (symbol) => {
      if (!symbol) return;
      setNewsStatus("Loading news…");
      clearNews();
      try {
        const res = await fetch(
          `/api/investor/news?symbol=${encodeURIComponent(symbol)}&limit=${encodeURIComponent(String(96))}`
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
          setNewsStatus("No recent news found.");
          return;
        }
        setNewsStatus(`${items.length} article${items.length === 1 ? "" : "s"} · newest first`);
        renderNews(items);
      } catch {
        setNewsStatus("Could not load news.");
        clearNews();
      } finally {
        void refreshInvestorDiagnostics();
      }
    };

    const setScoreLoading = () => {
      if (selectedScore) selectedScore.textContent = "Loading...";
      if (selectedRating) selectedRating.textContent = "Loading...";
      if (selectedBreakdown) selectedBreakdown.textContent = "Loading...";
      if (selectedExplanation) selectedExplanation.textContent = "Calculating evidence-based view...";
      if (selectedRisk) selectedRisk.textContent = "Assessing risk...";
    };

    const setScoreError = () => {
      if (selectedScore) selectedScore.textContent = "—";
      if (selectedRating) selectedRating.textContent = "—";
      if (selectedBreakdown) selectedBreakdown.textContent = "—";
      if (selectedExplanation) {
        selectedExplanation.textContent = "Scoring data is unavailable right now. Keep this ticker on watch.";
      }
      if (selectedRisk) selectedRisk.textContent = "Risk warning: market conditions can change quickly.";
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
        if (selectedRating) selectedRating.textContent = text(payload?.rating);
        if (selectedBreakdown) {
          const b = payload?.breakdown || {};
          selectedBreakdown.textContent = [
            `Trend ${text(b.trend_score)}`,
            `Momentum ${text(b.momentum_score)}`,
            `News ${text(b.news_score)}`,
            `Risk ${text(b.risk_score)}`,
            `Consistency ${text(b.consistency_score)}`,
            `Score ${text(b.overall_investor_score)}`,
          ].join(" · ");
        }
        if (selectedExplanation) {
          const why = typeof payload?.why_ranked === "string" ? payload.why_ranked.trim() : "";
          selectedExplanation.textContent = why || text(payload?.explanation);
        }
        if (selectedRisk) selectedRisk.textContent = text(payload?.risk_warning);
      } catch {
        setScoreError();
      }
    };

    const setSummaryLoading = () => {
      if (summaryHappening) summaryHappening.textContent = "Generating structured summary...";
      if (summaryWhy) summaryWhy.textContent = "Analyzing why this setup matters...";
      if (summaryHelp) summaryHelp.textContent = "Loading...";
      if (summaryHurt) summaryHurt.textContent = "Loading...";
      if (summaryConclusion) summaryConclusion.textContent = "Loading...";
      if (summarySources) summarySources.innerHTML = "";
    };

    const setSummaryError = () => {
      if (summaryHappening) summaryHappening.textContent = "Summary is unavailable right now.";
      if (summaryWhy) summaryWhy.textContent = "Try again after data refresh.";
      if (summaryHelp) summaryHelp.textContent = "—";
      if (summaryHurt) summaryHurt.textContent = "—";
      if (summaryConclusion) summaryConclusion.textContent = "Keep this ticker on watch.";
      if (summarySources) summarySources.innerHTML = "";
    };

    const renderSources = (sources) => {
      if (!summarySources) return;
      summarySources.innerHTML = "";
      if (!Array.isArray(sources) || !sources.length) {
        const empty = document.createElement("p");
        empty.className = "investor-source-row";
        empty.textContent = "No sources available.";
        summarySources.appendChild(empty);
        return;
      }
      sources.forEach((src) => {
        const row = document.createElement("div");
        row.className = "investor-source-row";
        const label = text(src?.label);
        const detail = text(src?.detail);
        const url = String(src?.url || "").trim();
        if (url) {
          const a = document.createElement("a");
          a.href = url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = `${label}: ${detail}`;
          row.appendChild(a);
        } else {
          row.textContent = `${label}: ${detail}`;
        }
        summarySources.appendChild(row);
      });
    };

    const loadResearchSummary = async (symbol) => {
      if (!symbol) return;
      setSummaryLoading();
      try {
        const res = await fetch(
          `/api/investor/research-summary?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(currentRange)}`
        );
        if (!res.ok) {
          setSummaryError();
          return;
        }
        const payload = await res.json();
        const sections = payload?.sections || {};
        if (summaryHappening) summaryHappening.textContent = text(sections.what_is_happening);
        if (summaryWhy) summaryWhy.textContent = text(sections.why_it_matters);
        if (summaryHelp) {
          const arr = Array.isArray(sections.what_could_help) ? sections.what_could_help : [];
          summaryHelp.textContent = arr.length ? arr.join(" ") : "—";
        }
        if (summaryHurt) {
          const arr = Array.isArray(sections.what_could_hurt) ? sections.what_could_hurt : [];
          summaryHurt.textContent = arr.length ? arr.join(" ") : "—";
        }
        if (summaryConclusion) summaryConclusion.textContent = text(sections.overall_conclusion);
        renderSources(payload?.sources);
      } catch {
        setSummaryError();
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
      let pct =
        typeof item?.period_return_percent === "number" && Number.isFinite(item.period_return_percent)
          ? item.period_return_percent
          : periodReturnPercentFromVals(vals);
      const trend = trendStrokeClassFromPct(pct);
      const pctText =
        pct === null || pct === undefined || !Number.isFinite(Number(pct))
          ? "Unavailable"
          : formatSignedPeriodPct(pct);
      const polyClass = `investor-opp-spark-line investor-opp-spark-line--${trend}`;
      const intervalHint = opportunitiesIntervalLabel || "Period";
      let svgInner;
      if (vals.length >= 2) {
        const attr = buildSparkPolylineAttr(vals, 120, 36, 2, 3);
        svgInner = `<svg class="investor-opp-spark-svg" viewBox="0 0 120 36" preserveAspectRatio="none" aria-hidden="true"><polyline class="${polyClass}" points="${attr}" /></svg>`;
      } else {
        svgInner = `<svg class="investor-opp-spark-svg investor-opp-spark-svg--empty" viewBox="0 0 120 36" aria-hidden="true"><text class="investor-opp-spark-empty" x="60" y="20" text-anchor="middle">Insufficient data</text></svg>`;
      }
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

    const ratingBadgeVariant = (item) => {
      const v = String(item?.rating_badge_variant || "").toLowerCase();
      if (v === "bullish" || v === "neutral" || v === "cautious") return v;
      const lab = String(item?.rating_badge || item?.rating || "").toLowerCase();
      if (lab.includes("bull")) return "bullish";
      if (lab.includes("caut")) return "cautious";
      return "neutral";
    };

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

      const unavailable = "Unavailable";
      const trimStr = (v) => (typeof v === "string" ? v.trim() : "");
      const ratingText = trimStr(item?.rating_badge) || trimStr(item?.rating) || unavailable;
      const scoreNum = Number(item?.score);
      const scoreText = Number.isFinite(scoreNum) ? String(scoreNum) : unavailable;
      const companyText = trimStr(item?.company_name) || unavailable;

      const formatUsd = (v) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return null;
        const frac = Math.abs(n) >= 1 ? 2 : 4;
        return `$${n.toLocaleString(undefined, { minimumFractionDigits: frac, maximumFractionDigits: frac })}`;
      };

      const formatSignedUsd = (v) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return null;
        const sign = n >= 0 ? "+" : "−";
        const abs = Math.abs(n);
        const frac = abs >= 1 ? 2 : 4;
        return `${sign}$${abs.toLocaleString(undefined, { minimumFractionDigits: frac, maximumFractionDigits: frac })}`;
      };

      const formatSignedPct = (v) => {
        const n = Number(v);
        if (!Number.isFinite(n)) return null;
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
        trimStr(item?.current_price_display) || formatUsd(item?.current_price) || unavailable;
      last.appendChild(lastLabel);
      last.appendChild(lastVal);

      const dt = deltaToneFromPct(item?.daily_change_percent);
      const deltas = document.createElement("div");
      deltas.className = "investor-opp-deltas";

      const usd = document.createElement("span");
      usd.className = `investor-opp-delta investor-opp-delta--${dt}`;
      usd.textContent =
        trimStr(item?.daily_change_dollars_display) || formatSignedUsd(item?.daily_change_dollar) || unavailable;

      const pctp = document.createElement("span");
      pctp.className = `investor-opp-delta investor-opp-delta--${dt}`;
      pctp.textContent =
        trimStr(item?.daily_change_percent_display) || formatSignedPct(item?.daily_change_percent) || unavailable;

      deltas.appendChild(usd);
      deltas.appendChild(pctp);
      priceWrap.appendChild(last);
      priceWrap.appendChild(deltas);

      const sparkHost = document.createElement("div");
      sparkHost.className = "investor-opp-spark-host";
      sparkHost.innerHTML = opportunitySparkRowHtml(item);

      const thesis = document.createElement("p");
      thesis.className = "investor-opp-thesis";
      const thesisText = typeof item?.reason_short === "string" ? item.reason_short.trim() : "";
      if (thesisText) {
        thesis.textContent = thesisText;
      } else {
        thesis.hidden = true;
      }

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
      card.appendChild(sparkHost);
      card.appendChild(thesis);
      card.appendChild(tierLine);
      card.appendChild(newsHead);
      card.appendChild(newsUl);
      card.appendChild(metrics);

      card.addEventListener("click", () => {
        setSelected({
          ticker: item?.ticker,
          company_name: item?.company_name,
          exchange: item?.exchange,
          asset_type: item?.asset_type,
        });
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
      if (oppsStatus) oppsStatus.textContent = "Scanning stock universe for top opportunities...";
      if (oppsList) oppsList.innerHTML = "";
      try {
        const res = await fetch("/api/investor/opportunities?limit=3&interval=6M");
        if (!res.ok) {
          if (oppsStatus) oppsStatus.textContent = "Unable to load recommendations right now.";
          void refreshInvestorDiagnostics();
          return;
        }
        const payload = await res.json();
        const items = Array.isArray(payload?.items) ? payload.items : [];
        opportunitiesIntervalLabel = typeof payload?.interval === "string" ? payload.interval : "6M";
        if (!items.length) {
          if (oppsStatus) oppsStatus.textContent = "No opportunities available right now.";
          void refreshInvestorDiagnostics();
          return;
        }
        if (oppsStatus) oppsStatus.textContent = `Top ${items.length} opportunities from screened individual stocks.`;
        renderOpportunities(items);
        void refreshInvestorDiagnostics();
      } catch {
        if (oppsStatus) oppsStatus.textContent = "Unable to load recommendations right now.";
        if (oppsList) oppsList.innerHTML = "";
        void refreshInvestorDiagnostics();
      }
    };

    const renderResults = (items) => {
      if (!searchMounted || !results) return;
      results.innerHTML = "";
      if (!Array.isArray(items) || !items.length) return;
      items.forEach((item) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "investor-search-result";
        row.setAttribute("role", "listitem");
        row.innerHTML = `
          <span class="investor-search-result-ticker">${text(item.ticker)}</span>
          <span class="investor-search-result-company">${text(item.company_name)}</span>
          <span class="investor-search-result-meta">${text(item.exchange)} • ${text(item.type || item.asset_type)}</span>
        `;
        row.addEventListener("click", () => setSelected(item));
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

