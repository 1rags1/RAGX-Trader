/**
 * Persistent top-level tab switching for Trader vs Investor views.
 * Keeps Trader DOM mounted so live sockets/chart continue running.
 */
(function () {
  "use strict";

  function mountTabs() {
    const tabButtons = Array.from(document.querySelectorAll(".app-tab[data-tab-target]"));
    if (!tabButtons.length) return;
    document.body.setAttribute("data-active-tab", "trader");

    const viewsByKey = {
      trader: document.getElementById("view-trader"),
      investor: document.getElementById("view-investor"),
    };

    const setActiveTab = (nextKey) => {
      if (!viewsByKey[nextKey]) return;
      tabButtons.forEach((btn) => {
        const key = btn.dataset.tabTarget;
        const isActive = key === nextKey;
        btn.classList.toggle("app-tab--active", isActive);
        btn.setAttribute("aria-selected", isActive ? "true" : "false");
        btn.tabIndex = isActive ? 0 : -1;
      });

      Object.entries(viewsByKey).forEach(([key, view]) => {
        if (!view) return;
        view.hidden = key !== nextKey;
      });
      document.body.setAttribute("data-active-tab", nextKey);

      window.dispatchEvent(new CustomEvent("ragx-active-tab", { detail: { tab: nextKey } }));

      // Hidden Trader chart needs resize once visible again.
      if (nextKey === "trader") {
        window.dispatchEvent(new Event("resize"));
      }
    };

    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => {
        setActiveTab(btn.dataset.tabTarget || "trader");
      });
      btn.addEventListener("keydown", (ev) => {
        if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
        ev.preventDefault();
        const activeIndex = tabButtons.findIndex((el) => el.classList.contains("app-tab--active"));
        const delta = ev.key === "ArrowRight" ? 1 : -1;
        const nextIndex = (activeIndex + delta + tabButtons.length) % tabButtons.length;
        const nextBtn = tabButtons[nextIndex];
        if (!nextBtn) return;
        nextBtn.focus();
        setActiveTab(nextBtn.dataset.tabTarget || "trader");
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountTabs);
  } else {
    mountTabs();
  }
})();
