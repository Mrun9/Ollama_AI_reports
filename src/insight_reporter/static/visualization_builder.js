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

  const singleMeasureCharts = new Set([
    "scatter",
    "histogram",
    "box",
    "pareto",
    "donut",
    "heatmap",
    "waterfall",
    "funnel",
    "scorecard",
  ]);
  const chartRules = {
    time_line: {
      allowedKinds: new Set(["date"]),
      label: "Which date should be used?",
      help: "The chart will place dates from this field in time order.",
      hideGrouping: false,
    },
    time_area: {
      allowedKinds: new Set(["date"]),
      label: "Which date should be used?",
      help: "The chart will emphasize change across this date field.",
      hideGrouping: false,
    },
    time_area_stacked: {
      allowedKinds: new Set(["date"]),
      label: "Which date should be used?",
      help: "Choose a date, then optionally split the total in Advanced options.",
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
    category_bar_stacked: {
      allowedKinds: new Set(["category"]),
      label: "Which groups should be compared?",
      help: "Choose a group, then split its composition in Advanced options.",
      hideGrouping: false,
    },
    pareto: {
      allowedKinds: new Set(["category"]),
      label: "Which contributors should be ranked?",
      help: "Categories will be sorted from largest to smallest automatically.",
      hideGrouping: false,
    },
    donut: {
      allowedKinds: new Set(["category"]),
      label: "Which categories make up the whole?",
      help: "Choose a field containing no more than seven category values.",
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
    heatmap: {
      allowedKinds: new Set(["category"]),
      label: "Which groups should run across the chart?",
      help: "A second group will create the heatmap rows.",
      hideGrouping: false,
    },
    waterfall: {
      allowedKinds: new Set(["category"]),
      label: "Which ordered steps explain the change?",
      help: "Choose a stage or ordered category field.",
      hideGrouping: false,
    },
    funnel: {
      allowedKinds: new Set(["category"]),
      label: "Which field contains the process stages?",
      help: "Stages will be compared from the largest value to the smallest.",
      hideGrouping: false,
    },
    combo: {
      allowedKinds: new Set(["category"]),
      label: "Which groups should compare the two measures?",
      help: "Select exactly two compatible numbers for bars and a line.",
      hideGrouping: false,
    },
    scorecard: {
      allowedKinds: new Set(["none"]),
      label: "No grouping is needed",
      help: "The selected value will be aggregated into one headline number.",
      hideGrouping: true,
    },
  };

  function selectedChart() {
    return chartChoices.find((choice) => choice.checked)?.value ?? "";
  }

  function limitMeasures(chartType) {
    const singleMeasure = singleMeasureCharts.has(chartType);
    if (measureHelp !== null) {
      measureHelp.textContent = chartType === "combo"
        ? "Select exactly two compatible numbers: one for bars and one for the line."
        : singleMeasure
        ? "This view uses one number. Selecting another will replace the current choice."
        : "Select one number, or up to five compatible numbers for comparison.";
    }
    if (chartType === "combo") {
      const checked = measures.filter((measure) => measure.checked);
      checked.slice(2).forEach((measure) => {
        measure.checked = false;
      });
      return;
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
      "time_area",
      "time_area_stacked",
      "category_bar",
      "category_bar_horizontal",
      "category_bar_stacked",
      "heatmap",
    ]).has(chartType);
    if (seriesField !== null) {
      seriesField.hidden = !supportsSeries;
    }
    if (!supportsSeries && seriesColumn !== null) {
      seriesColumn.value = "";
    }
    if (chartType === "heatmap" && seriesColumn !== null) {
      const alternatives = Array.from(seriesColumn.options).filter(
        (option) => option.value && option.value !== xColumn.value
      );
      if (!seriesColumn.value || seriesColumn.value === xColumn.value) {
        seriesColumn.value = alternatives[0]?.value ?? "";
      }
      const advanced = seriesField?.closest("details");
      if (advanced !== null && advanced !== undefined) {
        advanced.open = true;
      }
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
        if (measure.checked && selectedChart() === "combo") {
          const checked = measures.filter((item) => item.checked);
          if (checked.length > 2) {
            checked[0].checked = false;
          }
        }
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
