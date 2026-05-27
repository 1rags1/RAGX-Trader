/**
 * RAGX-Trader access gate — submit code, set session cookie, enter dashboard.
 */
(function () {
  "use strict";

  const form = document.getElementById("gateForm");
  const input = document.getElementById("gateCode");
  const errorEl = document.getElementById("gateError");
  const submitBtn = document.getElementById("gateSubmit");

  if (!form || !input) return;

  function showError(message) {
    if (!errorEl) return;
    errorEl.textContent = message;
    errorEl.hidden = !message;
  }

  async function redirectIfAuthenticated() {
    try {
      const res = await fetch("/api/gate/status", { credentials: "same-origin" });
      if (!res.ok) return;
      const data = await res.json();
      if (data && data.authenticated) {
        window.location.replace("/");
      }
    } catch {
      /* ignore */
    }
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    showError("");

    const code = (input.value || "").trim();
    if (!code) {
      showError("Enter your access code.");
      return;
    }

    submitBtn.disabled = true;

    try {
      const res = await fetch("/api/gate/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ code: code }),
      });

      if (res.ok) {
        window.location.replace("/");
        return;
      }

      let detail = "Incorrect access code.";
      try {
        const body = await res.json();
        if (body && body.detail) detail = body.detail;
      } catch {
        /* ignore */
      }
      showError(detail);
    } catch {
      showError("Could not reach the server. Try again.");
    } finally {
      submitBtn.disabled = false;
    }
  });

  redirectIfAuthenticated();
})();
