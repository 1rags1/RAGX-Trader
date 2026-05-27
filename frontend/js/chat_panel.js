(function () {
  "use strict";

  const state = {
    isMounted: false,
    isLoading: false,
    context: {
      market: "BTCUSDT",
      interval: "1m",
      backendConnected: false,
      feedConnected: null,
      lastCandle: null,
      indicators: null,
      strategy: null,
      lastUpdateIso: null,
    },
  };

  const dom = {
    history: null,
    form: null,
    input: null,
    send: null,
    loading: null,
    promptButtons: [],
  };

  function roleLabel(role) {
    if (role === "user") return "User";
    if (role === "system") return "System";
    return "Assistant";
  }

  function scrollToLatest() {
    if (!dom.history) return;
    dom.history.scrollTop = dom.history.scrollHeight;
  }

  function setLoading(isLoading) {
    state.isLoading = !!isLoading;
    if (dom.loading) dom.loading.hidden = !state.isLoading;
    if (dom.input) dom.input.disabled = state.isLoading;
    if (dom.send) dom.send.disabled = state.isLoading;
    scrollToLatest();
  }

  function appendMessage(role, text) {
    if (!dom.history) return;
    const msg = document.createElement("article");
    msg.className = `assistant-chat-msg assistant-chat-msg--${role}`;

    const roleEl = document.createElement("span");
    roleEl.className = "assistant-chat-msg-role";
    roleEl.textContent = roleLabel(role);

    const bodyEl = document.createElement("div");
    bodyEl.className = "assistant-chat-msg-body";
    bodyEl.textContent = String(text || "").trim();

    msg.appendChild(roleEl);
    msg.appendChild(bodyEl);
    dom.history.appendChild(msg);
    scrollToLatest();
  }

  function toNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function fmt(value, digits) {
    const n = toNumber(value);
    if (n === null) return "n/a";
    return n.toFixed(digits);
  }

  function signalFromStrategy() {
    const s = state.context.strategy;
    if (!s || typeof s !== "object") return "unknown";
    return String(s.final_signal || s.signal || "unknown").toLowerCase();
  }

  function confidenceFromStrategy() {
    const s = state.context.strategy;
    if (!s || typeof s !== "object") return "n/a";
    return fmt(s.final_confidence ?? s.confidence, 1) + "%";
  }

  function explainByIntent(promptText) {
    const text = String(promptText || "").toLowerCase();
    const c = state.context;
    const ind = c.indicators || {};
    const sig = signalFromStrategy();
    const conf = confidenceFromStrategy();
    const rsi = toNumber(ind.rsi);
    const macdHist = toNumber(ind.macd_hist);
    const close = toNumber(c.lastCandle && c.lastCandle.close);
    const open = toNumber(c.lastCandle && c.lastCandle.open);
    const change = close !== null && open !== null ? close - open : null;
    const changePct = change !== null && open ? (change / open) * 100 : null;

    if (text.includes("neutral")) {
      return [
        `Current signal is ${sig.toUpperCase()} with confidence ${conf}.`,
        rsi === null
          ? "RSI is still warming up or unavailable, so momentum context is limited."
          : `RSI is ${rsi.toFixed(1)}, which is near mid-range and not extreme.`,
        macdHist === null
          ? "MACD histogram is unavailable right now."
          : `MACD histogram is ${macdHist.toFixed(4)}; near zero usually means mixed momentum.`,
        "When trend, momentum, and reversal legs disagree, the engine often stays neutral.",
      ].join("\n");
    }

    if (text.includes("last few candles") || text.includes("changed")) {
      return [
        `Latest ${c.interval} candle close: ${fmt(close, 2)} (${c.market}).`,
        change === null
          ? "Candle delta is not available yet."
          : `Current candle move: ${change >= 0 ? "+" : ""}${fmt(change, 2)} (${changePct >= 0 ? "+" : ""}${fmt(changePct, 2)}%).`,
        `Signal now: ${sig.toUpperCase()} (${conf}).`,
        "If this candle closes with stronger follow-through, the score can shift on the next update.",
      ].join("\n");
    }

    if (text.includes("simply") || text.includes("explain")) {
      return [
        "Simple view:",
        `- The engine checks trend + indicators on ${c.market} (${c.interval}).`,
        `- Right now it sees: signal ${sig.toUpperCase()} at ${conf}.`,
        "- This is rule-based context, not a future price prediction.",
      ].join("\n");
    }

    return [
      `I can explain the live setup using current ${c.market} / ${c.interval} context.`,
      `Current signal: ${sig.toUpperCase()} (${conf}).`,
      "Try: 'Why is the signal neutral?' or 'What changed in the last few candles?'",
    ].join("\n");
  }

  function generateAssistantReply(userPrompt) {
    return new Promise((resolve) => {
      window.setTimeout(() => {
        resolve(explainByIntent(userPrompt));
      }, 620);
    });
  }

  async function handleSubmit(ev) {
    ev.preventDefault();
    if (!dom.input || state.isLoading) return;
    const promptText = String(dom.input.value || "").trim();
    if (!promptText) return;

    appendMessage("user", promptText);
    dom.input.value = "";
    setLoading(true);
    try {
      const reply = await generateAssistantReply(promptText);
      appendMessage("assistant", reply);
    } finally {
      setLoading(false);
      dom.input.focus();
    }
  }

  function onPromptChipClick(ev) {
    if (!dom.input || state.isLoading) return;
    const text = ev.currentTarget && ev.currentTarget.textContent ? ev.currentTarget.textContent.trim() : "";
    if (!text) return;
    dom.input.value = text;
    dom.input.focus();
  }

  const RagxChatPanel = {
    mount: function () {
      if (state.isMounted) return;
      dom.history = document.getElementById("assistant-chat-history");
      dom.form = document.getElementById("assistant-chat-form");
      dom.input = document.getElementById("assistant-chat-input");
      dom.send = document.getElementById("assistant-chat-send");
      dom.loading = document.getElementById("assistant-chat-loading");
      dom.promptButtons = Array.from(document.querySelectorAll("[data-chat-prompt]"));

      if (!dom.history || !dom.form || !dom.input || !dom.send || !dom.loading) return;

      dom.form.addEventListener("submit", handleSubmit);
      dom.promptButtons.forEach((btn) => btn.addEventListener("click", onPromptChipClick));

      appendMessage(
        "system",
        "Local assistant is active. Responses use in-app live context only. No external AI API is connected yet."
      );
      appendMessage("assistant", "Ask anything about this dashboard state and I will explain it in plain terms.");

      state.isMounted = true;
    },

    updateContext: function (partial) {
      if (!partial || typeof partial !== "object") return;
      state.context = Object.assign({}, state.context, partial);
    },
  };

  window.RagxChatPanel = RagxChatPanel;
})();
