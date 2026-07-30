(() => {
  "use strict";

  const chartChoices = Array.from(
    document.querySelectorAll('input[name="chart_type"]')
  );
  const measures = Array.from(
    document.querySelectorAll('input[name="measure_selectors"]')
  );
  const xColumn = document.querySelector("#x-column");
  const xColumnField = document.querySelector("#x-column-field");
  const xColumnLabel = document.querySelector("#x-column-label");
  const groupingHelp = document.querySelector("#grouping-help");
  const measureHelp = document.querySelector("#measure-help");
  const seriesField = document.querySelector("#series-field");
  const seriesColumn = document.querySelector("#series-column");

  if (
    chartChoices.length === 0 ||
    xColumn === null ||
    xColumnField === null
  ) {
    return;
  }

  const singleMeasureCharts = new Set(["scatter", "histogram", "box"]);
  const chartRules = {
    time_line: {
      allowedKinds: new Set(["date"]),
      label: "Which date should be used?",
      help: "The chart will place dates from this field in time order.",
      hideGrouping: false,
    },
    category_bar: {
      allowedKinds: new Set(["category"]),
      label: "Which groups should be compared?",
      help: "Choose a field such as region, product, team, or customer type.",
      hideGrouping: false,
    },
    category_bar_horizontal: {
      allowedKinds: new Set(["category"]),
      label: "Which groups should be ranked?",
      help: "Choose a field such as region, product, team, or customer type.",
      hideGrouping: false,
    },
    scatter: {
      allowedKinds: new Set(["numeric"]),
      label: "Which other number should it be compared with?",
      help: "Each point will compare the selected measure with this numeric field.",
      hideGrouping: false,
    },
    histogram: {
      allowedKinds: new Set(["none"]),
      label: "No grouping is needed",
      help: "The selected measure will be arranged into value ranges automatically.",
      hideGrouping: true,
    },
    box: {
      allowedKinds: new Set(["none", "category"]),
      label: "Compare the spread by a group? (optional)",
      help: "Choose a group to compare distributions, or leave it ungrouped.",
      hideGrouping: false,
    },
  };

  function selectedChart() {
    return chartChoices.find((choice) => choice.checked)?.value ?? "";
  }

  function limitMeasures(chartType) {
    const singleMeasure = singleMeasureCharts.has(chartType);
    if (measureHelp !== null) {
      measureHelp.textContent = singleMeasure
        ? "This view uses one number. Selecting another will replace the current choice."
        : "Select one number, or up to five compatible numbers for comparison.";
    }
    if (!singleMeasure) {
      return;
    }
    const checked = measures.filter((measure) => measure.checked);
    checked.slice(1).forEach((measure) => {
      measure.checked = false;
    });
  }

  function updateGrouping(chartType) {
    const rule = chartRules[chartType];
    if (rule === undefined) {
      return;
    }
    const options = Array.from(xColumn.querySelectorAll("option"));
    options.forEach((option) => {
      option.disabled = !rule.allowedKinds.has(option.dataset.kind);
    });
    const selectedOption = xColumn.selectedOptions[0];
    if (
      selectedOption === undefined ||
      selectedOption.disabled
    ) {
      const firstAllowed = options.find((option) => !option.disabled);
      xColumn.value = firstAllowed?.value ?? "";
    }
    xColumn.required = !rule.allowedKinds.has("none");
    xColumnField.hidden = rule.hideGrouping;
    if (xColumnLabel !== null) {
      xColumnLabel.textContent = rule.label;
    }
    if (groupingHelp !== null) {
      groupingHelp.textContent = rule.help;
    }

    const supportsSeries = new Set([
      "time_line",
      "category_bar",
      "category_bar_horizontal",
    ]).has(chartType);
    if (seriesField !== null) {
      seriesField.hidden = !supportsSeries;
    }
    if (!supportsSeries && seriesColumn !== null) {
      seriesColumn.value = "";
    }
  }

  function updateBuilder() {
    const chartType = selectedChart();
    limitMeasures(chartType);
    updateGrouping(chartType);
  }

  chartChoices.forEach((choice) => {
    choice.addEventListener("change", updateBuilder);
  });
  measures.forEach((measure) => {
    measure.addEventListener("change", () => {
      if (!measure.checked || !singleMeasureCharts.has(selectedChart())) {
        return;
      }
      measures.forEach((other) => {
        if (other !== measure) {
          other.checked = false;
        }
      });
    });
  });

  updateBuilder();
})();
