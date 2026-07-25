"use strict";

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-context-insert]");
  if (!button) {
    return;
  }
  const target = document.getElementById(button.dataset.contextTarget || "");
  if (!target || typeof target.value !== "string") {
    return;
  }
  const insertion = button.dataset.contextInsert || "";
  const start = Number.isInteger(target.selectionStart)
    ? target.selectionStart
    : target.value.length;
  const end = Number.isInteger(target.selectionEnd)
    ? target.selectionEnd
    : start;
  const mode = button.dataset.contextMode || "prompt";
  const prefix =
    mode === "formula" || start === 0 || /\s$/.test(target.value.slice(0, start))
      ? ""
      : " ";
  const suffix =
    mode === "formula" ||
    end === target.value.length ||
    /^\s/.test(target.value.slice(end))
      ? ""
      : " ";
  target.setRangeText(`${prefix}${insertion}${suffix}`, start, end, "end");
  target.focus();
  target.dispatchEvent(new Event("input", { bubbles: true }));
});
