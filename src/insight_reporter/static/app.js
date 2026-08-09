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
    const liveRegion = document.getElementById("app-live-region");
    const announce = (message) => {
      if (!liveRegion) return;
      liveRegion.textContent = "";
      window.requestAnimationFrame(() => {
        liveRegion.textContent = message;
      });
    };

    document.querySelectorAll("form").forEach((form) => {
      form.addEventListener(
        "invalid",
        () => {
          form.classList.add("was-validated");
          let message = form.querySelector("[data-validation-message]");
          if (!message) {
            message = document.createElement("p");
            message.className = "app-validation-message";
            message.dataset.validationMessage = "";
            message.setAttribute("role", "alert");
            message.textContent = "Review the highlighted field before continuing.";
            form.append(message);
          }
          announce("The form needs a correction before it can be submitted.");
        },
        true,
      );
    });

    document.querySelectorAll("[data-file-input]").forEach((input) => {
      const targetId = input.dataset.fileInput;
      const target = targetId ? document.getElementById(targetId) : null;
      if (!target) return;

      input.addEventListener("change", () => {
        const file = input.files && input.files[0];
        target.textContent = file ? file.name : "No file selected";
      });
    });

    document.querySelectorAll("form[data-confirm]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        if (event.defaultPrevented) return;
        const message = form.dataset.confirm || "Continue with this action?";
        if (!window.confirm(message)) event.preventDefault();
      });
    });

    document.querySelectorAll("form[data-loading-form]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        if (event.defaultPrevented || !form.checkValidity()) return;

        form.setAttribute("aria-busy", "true");
        const loadingMessage =
          event.submitter?.dataset.loadingText || "Working on your request…";
        announce(loadingMessage);
        form.querySelectorAll("button[type='submit'], input[type='submit']").forEach((button) => {
          button.disabled = true;
          const loadingText = button.dataset.loadingText;
          if (!loadingText || button.tagName !== "BUTTON") return;

          button.textContent = "";
          const label = document.createElement("span");
          label.className = "app-loading-label";
          const spinner = document.createElement("span");
          spinner.className = "spinner-border spinner-border-sm";
          spinner.setAttribute("aria-hidden", "true");
          label.append(spinner, document.createTextNode(loadingText));
          button.append(label);
        });
      });
    });

    const scrollTopButton = document.querySelector("[data-scroll-top]");
    if (scrollTopButton) {
      const updateScrollTopButton = () => {
        scrollTopButton.hidden = window.scrollY < 640;
      };
      window.addEventListener("scroll", updateScrollTopButton, { passive: true });
      scrollTopButton.addEventListener("click", () => {
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
        document.getElementById("main-content")?.focus({ preventScroll: true });
      });
      updateScrollTopButton();
    }

    if (window.bootstrap) {
      document.querySelectorAll("[data-bs-toggle='tooltip']").forEach((element) => {
        window.bootstrap.Tooltip.getOrCreateInstance(element);
      });
    }
  });
})();
