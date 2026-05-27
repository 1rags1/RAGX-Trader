/**
 * Timeframe manager — UI control + POST /api/timeframe for switching Binance interval.
 */

(function (global) {
  "use strict";

  var ORDER = ["1m", "5m", "10m", "15m", "30m", "1d"];

  var _current = "1m";
  var _switching = false;

  function getCurrent() {
    return _current;
  }

  function setCurrent(interval) {
    if (ORDER.indexOf(interval) >= 0) _current = interval;
  }

  function labelFor(interval) {
    return interval;
  }

  function mountToolbar(container, onIntervalChange) {
    if (!container) return;
    container.innerHTML = "";
    container.setAttribute("role", "toolbar");
    container.setAttribute("aria-label", "Chart timeframe");
    var group = document.createElement("div");
    group.className = "tf-group";

    ORDER.forEach(function (iv) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "tf-btn";
      btn.dataset.interval = iv;
      btn.textContent = iv;
      btn.setAttribute("aria-pressed", iv === _current ? "true" : "false");
      btn.addEventListener("click", function () {
        if (_switching || iv === _current) return;
        void switchTimeframe(iv, onIntervalChange, btn);
      });
      group.appendChild(btn);
    });

    var badge = document.createElement("span");
    badge.className = "tf-badge";
    badge.id = "tf-active-label";
    badge.textContent = "TF: " + labelFor(_current);

    container.appendChild(group);
    container.appendChild(badge);
    syncToolbarPressed();
  }

  function syncToolbarPressed() {
    var root = document.getElementById("chart-toolbar");
    if (!root) return;
    root.querySelectorAll(".tf-btn").forEach(function (b) {
      b.setAttribute("aria-pressed", b.dataset.interval === _current ? "true" : "false");
      b.classList.toggle("tf-btn-active", b.dataset.interval === _current);
    });
    var badge = document.getElementById("tf-active-label");
    if (badge) badge.textContent = "TF: " + labelFor(_current);
  }

  /**
   * POST /api/timeframe; server cancels Binance WS, REST-seeds 200 bars, starts new stream.
   */
  async function switchTimeframe(interval, onIntervalChange, triggerBtn) {
    if (_switching) return false;
    _switching = true;
    if (triggerBtn) triggerBtn.disabled = true;
    try {
      var r = await fetch("/api/timeframe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interval: interval }),
      });
      if (!r.ok) {
        var errText = await r.text();
        console.warn("timeframe switch failed", r.status, errText);
        return false;
      }
      var j = await r.json();
      _current = j.interval || interval;
      if (typeof onIntervalChange === "function") {
        onIntervalChange(_current, j.bars || []);
      }
      syncToolbarPressed();
      return true;
    } catch (e) {
      console.warn("timeframe switch", e);
      return false;
    } finally {
      _switching = false;
      if (triggerBtn) triggerBtn.disabled = false;
    }
  }

  global.RagxTimeframe = {
    ORDER,
    getCurrent,
    setCurrent,
    labelFor,
    mountToolbar,
    syncToolbarPressed,
    switchTimeframe,
    isSwitching: function () {
      return _switching;
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
