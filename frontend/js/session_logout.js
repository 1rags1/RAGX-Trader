/**
 * RAGX-Trader session logout — clears gate cookie and returns to /gate.
 */
(function () {
  "use strict";

  const btn = document.getElementById("btn-logout");
  if (!btn) return;

  btn.addEventListener("click", async function () {
    btn.disabled = true;
    try {
      await fetch("/api/gate/logout", {
        method: "POST",
        credentials: "same-origin",
      });
    } catch {
      /* still redirect */
    }
    window.location.href = "/gate";
  });
})();
