"use strict";

(() => {
  const ready = (callback) => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
      return;
    }
    callback();
  };

  ready(() => {
    const widget = document.querySelector("[data-chat-widget]");
    if (!widget) return;

    const datasetId = widget.dataset.datasetId;
    const toggle = widget.querySelector("[data-chat-toggle]");
    const close = widget.querySelector("[data-chat-close]");
    const panel = widget.querySelector("[data-chat-panel]");
    const form = widget.querySelector("[data-chat-form]");
    const input = form?.querySelector("textarea[name='question']");
    const messages = widget.querySelector("[data-chat-messages]");
    if (!datasetId || !toggle || !panel || !form || !input || !messages) return;
    let historyLoaded = false;

    const setOpen = (open) => {
      panel.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) input.focus();
    };

    const appendMessage = (text, type) => {
      const message = document.createElement("div");
      message.className = `data-chat-message data-chat-message-${type}`;
      message.textContent = text;
      messages.append(message);
      messages.scrollTop = messages.scrollHeight;
      return message;
    };

    const appendDetails = (insights) => {
      if (!Array.isArray(insights) || insights.length === 0) return;
      const details = document.createElement("details");
      details.className = "data-chat-details";
      const summary = document.createElement("summary");
      summary.textContent = "Computed evidence";
      details.append(summary);
      const list = document.createElement("ul");
      insights.slice(0, 4).forEach((insight) => {
        const item = document.createElement("li");
        item.textContent = insight.finding || insight.title || "Computed finding";
        list.append(item);
      });
      details.append(list);
      messages.append(details);
      messages.scrollTop = messages.scrollHeight;
    };

    const appendStatus = (payload) => {
      if (payload.model_status !== "fallback_after_planner_error") return;
      const reason = payload.planner_error
        ? ` Planner fallback: ${payload.planner_error}`
        : " Planner fallback was used.";
      appendMessage(reason, "system");
    };

    const appendSavedEvidence = (payload) => {
      if (payload.saved_evidence_id) {
        const message = appendMessage(
          `Saved with verified evidence (${payload.saved_evidence_id}).`,
          "system",
        );
        const link = document.createElement("a");
        link.href = `/reports/${datasetId}/configure`;
        link.textContent = "Choose it for the final report";
        link.className = "d-block mt-1";
        message.append(link);
      } else if (payload.save_error) {
        appendMessage(`Answer shown, but it was not saved: ${payload.save_error}`, "system");
      }
    };

    const appendSuggestions = (suggestions) => {
      if (!Array.isArray(suggestions) || suggestions.length === 0) return;
      const container = document.createElement("div");
      container.className = "data-chat-suggestions";
      suggestions.slice(0, 3).forEach((suggestion) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn btn-sm btn-outline-secondary";
        button.textContent = suggestion;
        button.addEventListener("click", () => {
          input.value = suggestion;
          form.requestSubmit();
        });
        container.append(button);
      });
      messages.append(container);
      messages.scrollTop = messages.scrollHeight;
    };

    const loadHistory = async () => {
      if (historyLoaded) return;
      historyLoaded = true;
      try {
        const response = await fetch(`/api/workspaces/${datasetId}/chat/history`);
        const payload = await response.json();
        if (!response.ok || !Array.isArray(payload.history) || payload.history.length === 0) {
          return;
        }
        appendMessage("Restored saved chat history.", "system");
        payload.history.forEach((turn) => {
          appendMessage(turn.question, "user");
          appendMessage(turn.answer, "assistant");
          appendDetails(turn.insights);
          appendSavedEvidence(turn);
        });
      } catch (_error) {
        historyLoaded = false;
      }
    };

    toggle.addEventListener("click", () => {
      const opening = panel.hidden;
      setOpen(opening);
      if (opening) loadHistory();
    });
    close?.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !panel.hidden) {
        setOpen(false);
        toggle.focus();
      }
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const question = input.value.trim();
      if (!question) return;
      appendMessage(question, "user");
      input.value = "";
      const pending = appendMessage("Analyzing with DuckDB…", "system");
      const button = form.querySelector("button[type='submit']");
      if (button) button.disabled = true;
      try {
        const response = await fetch(`/api/workspaces/${datasetId}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ question }),
        });
        const payload = await response.json();
        pending.remove();
        if (!response.ok) {
          appendMessage(payload.error || "Data chat could not answer that.", "system");
          return;
        }
        appendMessage(payload.answer, "assistant");
        appendStatus(payload);
        appendDetails(payload.insights);
        appendSavedEvidence(payload);
        appendSuggestions(payload.suggested_questions);
      } catch (_error) {
        pending.remove();
        appendMessage("Data chat is unavailable right now.", "system");
      } finally {
        if (button) button.disabled = false;
        input.focus();
      }
    });
  });
})();
