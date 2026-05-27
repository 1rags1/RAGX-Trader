/**
 * Signal strength labels and copy — aligned with backend confidence bands:
 * low ≤ 39, medium ≤ 69, high ≥ 70 (see backend/explanation_payload.py).
 * Sell uses two public tiers: Weak (low+medium) vs Strong (high).
 */

(function (global) {
  "use strict";

  var LOW_MAX = 39;
  var MED_MAX = 69;

  function clampConf(c) {
    var n = Math.round(Number(c));
    if (!Number.isFinite(n)) return 0;
    return Math.max(0, Math.min(100, n));
  }

  /** @returns {"low"|"medium"|"high"} */
  function band(conf) {
    var c = clampConf(conf);
    if (c <= LOW_MAX) return "low";
    if (c <= MED_MAX) return "medium";
    return "high";
  }

  /**
   * @returns {string} e.g. "Strong Buy", or "" for neutral/exit/warmup
   */
  function getStrengthLabel(sig, conf) {
    var s = String(sig || "").toLowerCase();
    if (s === "buy") {
      var b = band(conf);
      if (b === "low") return "Weak Buy";
      if (b === "medium") return "Moderate Buy";
      return "Strong Buy";
    }
    if (s === "sell") {
      return band(conf) === "high" ? "Strong Sell" : "Weak Sell";
    }
    return "";
  }

  /**
   * Uses explanation_payload.trend.direction when present (same as backend).
   */
  function getMarketContextNote(explanationPayload) {
    if (!explanationPayload || typeof explanationPayload !== "object") return "";
    var t = explanationPayload.trend || {};
    var dir = String(t.direction || "unknown").toLowerCase();
    if (dir === "sideways" || dir === "unknown") {
      return "Market is choppy — signals less reliable.";
    }
    if (dir === "up" || dir === "down") {
      return "Best in trending markets.";
    }
    return "";
  }

  function buildGlanceLine(sig, conf) {
    var s = String(sig || "").toLowerCase();
    var b = band(conf);
    if (s === "buy") {
      if (b === "high") {
        return "Clear bullish alignment — rules agree strongly; still not a forecast.";
      }
      if (b === "medium") {
        return "Solid buy lean — useful when price and trend cooperate; confirm your risk.";
      }
      return "Soft buy edge — rules disagree enough to stay cautious.";
    }
    if (s === "sell") {
      if (b === "high") {
        return "Clear bearish alignment — rules agree strongly; still not a forecast.";
      }
      return "Cautious sell lean — treat as context, not a trigger.";
    }
    return "";
  }

  /**
   * Prepends strength-aware wording to the engine explanation (buy/sell only).
   */
  function applyWhyPrefix(sig, conf, engineExplanation) {
    var core = String(engineExplanation || "").trim();
    if (!core) return "—";
    var s = String(sig || "").toLowerCase();
    if (s !== "buy" && s !== "sell") return core;
    var b = band(conf);
    var prefix = "";
    if (s === "buy") {
      if (b === "high") prefix = "With strong conviction, ";
      else if (b === "medium") prefix = "With moderate conviction, ";
      else prefix = "With only a weak edge, ";
    } else {
      if (b === "high") prefix = "With strong conviction on the short side, ";
      else prefix = "With a cautious short bias, ";
    }
    var first = core.charAt(0).toLowerCase();
    var rest = core.slice(1);
    return prefix + first + rest;
  }

  global.RagxSignalStrength = {
    band: band,
    getStrengthLabel: getStrengthLabel,
    getMarketContextNote: getMarketContextNote,
    buildGlanceLine: buildGlanceLine,
    applyWhyPrefix: applyWhyPrefix,
    LOW_MAX: LOW_MAX,
    MED_MAX: MED_MAX,
  };
})(typeof window !== "undefined" ? window : globalThis);
