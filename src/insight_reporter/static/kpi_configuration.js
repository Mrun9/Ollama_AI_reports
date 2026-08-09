"use strict";

(() => {
  const ready = (callback) => {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
      return;
    }
    callback();
  };

  const updateSelectionCount = (container) => {
    const output = container.querySelector("[data-selection-count]");
    if (!output) return;
    const checked = container.querySelectorAll("input[type='checkbox']:checked:not(:disabled)").length;
    const maximum = output.dataset.selectionMaximum;
    output.textContent = maximum ? `${checked} of ${maximum} selected` : `${checked} selected`;
  };

  ready(() => {
    document.querySelectorAll("[data-table-search]").forEach((input) => {
      const table = document.getElementById(input.dataset.tableSearch || "");
      if (!table) return;
      const rows = table.querySelectorAll("tbody tr");
      const emptyMessage = document.querySelector(`[data-table-empty='${table.id}']`);

      const filterRows = () => {
        const query = input.value.trim().toLocaleLowerCase();
        let visible = 0;
        rows.forEach((row) => {
          const text = (row.dataset.searchText || row.textContent || "").toLocaleLowerCase();
          const matches = !query || text.includes(query);
          row.classList.toggle("is-filtered", !matches);
          if (matches) visible += 1;
        });
        if (emptyMessage) emptyMessage.hidden = visible !== 0;
      };

      input.addEventListener("input", filterRows);
      filterRows();
    });

    document.querySelectorAll("[data-selection-group]").forEach((container) => {
      container.addEventListener("change", () => updateSelectionCount(container));
      updateSelectionCount(container);
    });

    document.querySelectorAll("[data-conditional-form]").forEach((form) => {
      const valueColumnField = form.querySelector("[data-value-column-field]");
      const valueColumn = valueColumnField?.querySelector("select");
      const conditionColumn = form.querySelector("[name='condition_column']");
      const groups = form.querySelectorAll("[data-condition-values]");

      const updateBase = () => {
        const base = form.querySelector("[name='calculation_base']:checked")?.value;
        const isValueShare = base === "value_sum";
        if (valueColumnField) valueColumnField.hidden = !isValueShare;
        if (valueColumn) {
          valueColumn.disabled = !isValueShare;
          valueColumn.required = isValueShare;
        }
      };

      const updateCondition = () => {
        const selected = conditionColumn?.value || "";
        groups.forEach((group) => {
          const active = group.dataset.conditionValues === selected;
          group.hidden = !active;
          group.querySelectorAll("input").forEach((input) => {
            input.disabled = !active;
          });
        });
      };

      form.querySelectorAll("[name='calculation_base']").forEach((input) => {
        input.addEventListener("change", updateBase);
      });
      conditionColumn?.addEventListener("change", updateCondition);
      updateBase();
      updateCondition();
    });

    document.querySelectorAll("[data-derived-form]").forEach((form) => {
      const level = form.querySelector("[name='calculation_level']");
      const aggregation = form.querySelector("[name='aggregation']");
      const rowScope = form.querySelector("[name='target_scope'] option[value='row']");
      const hint = form.querySelector("[data-calculation-hint]");
      if (!level || !aggregation) return;

      const updateCalculation = () => {
        const aggregateLevel = level.value === "aggregate";
        if (aggregateLevel) aggregation.value = "formula";
        aggregation.querySelectorAll("option").forEach((option) => {
          option.disabled = aggregateLevel ? option.value !== "formula" : option.value === "formula";
        });
        if (rowScope) rowScope.disabled = aggregateLevel;
        const scope = form.querySelector("[name='target_scope']");
        if (aggregateLevel && scope?.value === "row") scope.value = "dataset";
        if (hint) {
          hint.textContent = aggregateLevel
            ? "Aggregate formulas use formula aggregation and cannot use a per-row target."
            : "Row formulas are calculated per record, then summarized using your chosen aggregation.";
        }
      };

      level.addEventListener("change", updateCalculation);
      updateCalculation();
    });
  });
})();
