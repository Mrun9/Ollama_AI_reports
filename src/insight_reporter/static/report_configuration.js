"use strict";

document.addEventListener("DOMContentLoaded", () => {
  const kpiInputs = document.querySelectorAll("[data-report-kpi]");
  const evidenceInputs = document.querySelectorAll(
    "[data-report-evidence-metric]",
  );
  const visualizationInputs = document.querySelectorAll(
    "[data-report-visualization-kpis]",
  );

  const evidenceFor = (metricId) =>
    Array.from(evidenceInputs).filter(
      (input) => input.dataset.reportEvidenceMetric === metricId,
    );

  const synchronizeEvidence = (kpiInput, selectAll) => {
    for (const evidenceInput of evidenceFor(kpiInput.dataset.reportKpi)) {
      evidenceInput.disabled = !kpiInput.checked;
      if (!kpiInput.checked) {
        evidenceInput.checked = false;
      } else if (
        selectAll &&
        evidenceInput.dataset.reportEvidenceRecommended === "true"
      ) {
        evidenceInput.checked = true;
      }
    }
  };

  const requiredKpis = (visualizationInput) =>
    visualizationInput.dataset.reportVisualizationKpis
      .split(",")
      .filter((metricId) => metricId);

  const selectVisualizationKpis = (visualizationInput) => {
    if (!visualizationInput.checked) {
      return;
    }
    for (const metricId of requiredKpis(visualizationInput)) {
      const kpiInput = Array.from(kpiInputs).find(
        (input) => input.dataset.reportKpi === metricId,
      );
      if (kpiInput !== undefined && !kpiInput.checked) {
        kpiInput.checked = true;
        synchronizeEvidence(kpiInput, true);
      }
    }
  };

  for (const kpiInput of kpiInputs) {
    synchronizeEvidence(kpiInput, false);
    kpiInput.addEventListener("change", () => {
      synchronizeEvidence(kpiInput, true);
      if (!kpiInput.checked) {
        for (const visualizationInput of visualizationInputs) {
          if (requiredKpis(visualizationInput).includes(kpiInput.dataset.reportKpi)) {
            visualizationInput.checked = false;
          }
        }
      }
    });
  }

  for (const visualizationInput of visualizationInputs) {
    selectVisualizationKpis(visualizationInput);
    visualizationInput.addEventListener("change", () => {
      selectVisualizationKpis(visualizationInput);
    });
  }
});
