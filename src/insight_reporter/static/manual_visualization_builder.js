(() => {
  "use strict";

  const board = document.querySelector("#manual-visualization-board");
  if (board === null) {
    return;
  }

  const fields = Array.from(board.querySelectorAll(".field-card"));
  const fieldGroups = Array.from(board.querySelectorAll("[data-field-group]"));
  const chartChoices = Array.from(board.querySelectorAll(".chart-choice"));
  const search = board.querySelector("#field-search");
  const xZone = board.querySelector("#x-axis-zone");
  const yZone = board.querySelector("#y-axis-zone");
  const seriesZone = board.querySelector("#legend-zone");
  const sizeZone = board.querySelector("#size-zone");
  const secondaryYZone = board.querySelector("#secondary-y-zone");
  const secondaryYFieldGroup = board.querySelector("#secondary-y-field-group");
  const secondaryYAxisWell = board.querySelector("#secondary-y-axis-well");
  const paretoLineSetting = board.querySelector("#pareto-line-setting");
  const paretoLineMode = board.querySelector("#pareto-line-mode");
  const targetSetting = board.querySelector("#target-setting");
  const targetValue = board.querySelector("#target-value");
  const noChartSettings = board.querySelector("#no-chart-settings");
  const saveButton = board.querySelector("#save-visualization");
  const titleInput = board.querySelector("#canvas-title");
  const initialStateElement = board.querySelector("#manual-initial-state");
  const emptyState = board.querySelector("#empty-state");
  const preview = board.querySelector("#chart-preview");
  const message = board.querySelector("#chart-message");
  const metadata = board.querySelector("#chart-meta");
  const status = board.querySelector("#chart-status");
  const previewUrl = board.dataset.previewUrl;
  const saveUrl = board.dataset.saveUrl;

  if (
    search === null || xZone === null || yZone === null ||
    seriesZone === null || sizeZone === null || secondaryYZone === null ||
    secondaryYFieldGroup === null || secondaryYAxisWell === null ||
    paretoLineSetting === null || paretoLineMode === null ||
    targetSetting === null || targetValue === null || noChartSettings === null ||
    saveButton === null || titleInput === null || initialStateElement === null ||
    emptyState === null || preview === null || message === null ||
    metadata === null || status === null ||
    previewUrl === undefined || saveUrl === undefined
  ) {
    return;
  }

  let initialState = {};
  try {
    initialState = JSON.parse(initialStateElement.textContent || "{}");
  } catch (_error) {
    initialState = {};
  }
  const initialFields = initialState.fields ?? {};
  const fieldState = (role) => {
    const name = initialFields[role];
    if (typeof name !== "string" || name === "") {
      return null;
    }
    const matching = fields.find(
      (field) => field.dataset.fieldName === name && field.dataset.preferredAxis === role
    ) ?? fields.find((field) => field.dataset.fieldName === name);
    return matching === undefined ? null : fieldFromElement(matching);
  };
  const state = {
    x: fieldState("x"),
    y: fieldState("y"),
    series: fieldState("series"),
    size: fieldState("size"),
    secondary_y: fieldState("secondary_y"),
    chart: typeof initialState.chart === "string" ? initialState.chart : "auto",
    paretoLine: initialState.settings?.pareto_line ?? "cumulative_percent",
    target: initialState.settings?.target ?? "",
  };
  let visualizationId = initialState.visualization_id ?? null;
  let currentPreviewPayload = null;
  const ignoredClicks = new WeakSet();
  let previewRequest = null;
  let drag = null;

  const chartLabels = {
    auto: "Automatic chart",
    column: "Column chart",
    bar: "Horizontal bar chart",
    stacked_column: "Stacked column chart",
    stacked_bar: "Stacked horizontal bar chart",
    grouped_column: "Grouped column chart",
    stacked_100_column: "100% stacked column",
    stacked_100_bar: "100% stacked bar",
    line: "Line chart",
    area: "Area chart",
    scatter: "Scatter plot",
    bubble: "Bubble chart",
    multi_line: "Multi-series line chart",
    combo: "Column and line combo",
    pie: "Pie chart",
    donut: "Donut chart",
    histogram: "Histogram",
    card: "KPI card",
    table: "Table",
    pareto: "Pareto chart",
    waterfall: "Waterfall chart",
    funnel: "Funnel chart",
    treemap: "Treemap",
    box: "Box plot",
    heatmap: "Heatmap",
    radar: "Radar chart",
    gauge: "Gauge",
    bullet: "Bullet chart",
  };

  if (typeof initialState.title === "string" && initialState.title !== "") {
    titleInput.value = initialState.title;
  }
  paretoLineMode.value = state.paretoLine;
  targetValue.value = state.target;
  saveButton.textContent = visualizationId === null
    ? "Save visualization"
    : "Update visualization";
  chartChoices.forEach((choice) => {
    const selected = choice.dataset.chartType === state.chart;
    choice.classList.toggle("is-selected", selected);
    choice.setAttribute("aria-pressed", String(selected));
  });

  function fieldFromElement(element) {
    return {
      name: element.dataset.fieldName ?? "",
      kind: element.dataset.fieldKind ?? "",
      preferredAxis: element.dataset.preferredAxis ?? "",
    };
  }

  function isNumeric(field) {
    return field?.kind === "numeric";
  }

  function assignAutomatically(field) {
    if (["x", "y", "series", "size", "secondary_y"].includes(field.preferredAxis)) {
      setAxis(field.preferredAxis, field);
      return;
    }
    if (isNumeric(field)) {
      if (state.y === null) {
        setAxis("y", field);
      } else if (state.x === null) {
        setAxis("x", field);
      } else {
        setAxis("y", field);
      }
      return;
    }
    if (state.x === null) {
      setAxis("x", field);
    } else if (state.series === null && field.name !== state.x.name) {
      setAxis("series", field);
    } else {
      setAxis("x", field);
    }
  }

  function setAxis(axis, field) {
    if (axis === "auto") {
      assignAutomatically(field);
      return;
    }
    state[axis] = field;
    renderAxisWells();
    refreshPreview();
  }

  function removeAxis(axis) {
    state[axis] = null;
    renderAxisWells();
    refreshPreview();
  }

  function renderAxisWells() {
    renderAxisWell(xZone, "x", "Drop a category, date, or number");
    renderAxisWell(yZone, "y", "Drop a numeric field");
    renderAxisWell(seriesZone, "series", "Drop a category to split the values");
    renderAxisWell(sizeZone, "size", "Drop a positive numeric field for bubbles");
    renderAxisWell(secondaryYZone, "secondary_y", "Drop the numeric line value");
    fields.forEach((field) => {
      const axis = field.dataset.preferredAxis;
      field.classList.toggle(
        "is-assigned",
        state[axis]?.name === field.dataset.fieldName
      );
    });
  }

  function renderAxisWell(zone, axis, placeholderText) {
    zone.replaceChildren();
    const field = state[axis];
    if (field === null) {
      const placeholder = document.createElement("span");
      placeholder.className = "axis-placeholder";
      placeholder.textContent = placeholderText;
      zone.append(placeholder);
      return;
    }
    const pill = document.createElement("span");
    pill.className = "field-pill";
    const name = document.createElement("span");
    name.textContent = field.name;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "×";
    const axisLabel = axis === "series"
      ? "Legend"
      : axis === "size"
      ? "Size"
      : axis === "secondary_y"
      ? "Secondary Y"
      : `${axis.toUpperCase()}-axis`;
    remove.setAttribute("aria-label", `Remove ${field.name} from ${axisLabel}`);
    remove.addEventListener("click", () => removeAxis(axis));
    pill.append(name, remove);
    zone.append(pill);
  }

  function beginPointerDrag(event, element) {
    if (event.button !== 0) {
      return;
    }
    drag = {
      element,
      field: fieldFromElement(element),
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
      ghost: null,
      target: null,
    };
    element.setPointerCapture(event.pointerId);
  }

  function movePointerDrag(event) {
    if (drag === null || drag.pointerId !== event.pointerId) {
      return;
    }
    const distance = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
    if (!drag.moved && distance < 5) {
      return;
    }
    event.preventDefault();
    if (!drag.moved) {
      drag.moved = true;
      drag.ghost = document.createElement("div");
      drag.ghost.className = "drag-ghost";
      drag.ghost.textContent = drag.field.name;
      document.body.append(drag.ghost);
    }
    drag.ghost.style.left = `${event.clientX}px`;
    drag.ghost.style.top = `${event.clientY}px`;
    const target = document.elementFromPoint(event.clientX, event.clientY)?.closest("[data-drop-zone]");
    if (drag.target !== target) {
      drag.target?.classList.remove("is-target");
      drag.target = target ?? null;
      drag.target?.classList.add("is-target");
    }
  }

  function finishPointerDrag(event) {
    if (drag === null || drag.pointerId !== event.pointerId) {
      return;
    }
    const finished = drag;
    drag = null;
    finished.target?.classList.remove("is-target");
    finished.ghost?.remove();
    if (finished.element.hasPointerCapture(event.pointerId)) {
      finished.element.releasePointerCapture(event.pointerId);
    }
    if (!finished.moved) {
      return;
    }
    ignoredClicks.add(finished.element);
    window.setTimeout(() => ignoredClicks.delete(finished.element), 0);
    const axis = finished.target?.dataset.dropZone;
    if (
      axis === "x" || axis === "y" || axis === "series" ||
      axis === "size" || axis === "secondary_y" || axis === "auto"
    ) {
      setAxis(axis, finished.field);
    }
  }

  fields.forEach((field) => {
    field.addEventListener("pointerdown", (event) => beginPointerDrag(event, field));
    field.addEventListener("pointermove", movePointerDrag);
    field.addEventListener("pointerup", finishPointerDrag);
    field.addEventListener("pointercancel", finishPointerDrag);
    field.addEventListener("click", () => {
      if (!ignoredClicks.has(field)) {
        assignAutomatically(fieldFromElement(field));
      }
    });
  });

  search.addEventListener("input", () => {
    const query = search.value.trim().toLocaleLowerCase();
    fields.forEach((field) => {
      const name = (field.dataset.fieldName ?? "").toLocaleLowerCase();
      field.hidden = query !== "" && !name.includes(query);
    });
    fieldGroups.forEach((group) => {
      const visibleFields = Array.from(group.querySelectorAll(".field-card")).some(
        (field) => !field.hidden
      );
      const inactiveComboRole = (
        group.dataset.fieldGroup === "secondary_y" && state.chart !== "combo"
      );
      group.hidden = inactiveComboRole || (query !== "" && !visibleFields);
    });
  });

  chartChoices.forEach((choice) => {
    const label = document.createElement("span");
    label.className = "chart-choice-label";
    label.textContent = choice.title || chartLabels[choice.dataset.chartType] || "Chart";
    choice.append(label);
    choice.addEventListener("click", () => {
      state.chart = choice.dataset.chartType ?? "auto";
      if (state.chart !== "combo" && state.secondary_y !== null) {
        state.secondary_y = null;
        renderAxisWells();
      }
      chartChoices.forEach((item) => {
        const selected = item === choice;
        item.classList.toggle("is-selected", selected);
        item.setAttribute("aria-pressed", String(selected));
      });
      updateChartSettingsVisibility();
      refreshPreview();
    });
  });

  paretoLineMode.addEventListener("change", () => {
    state.paretoLine = paretoLineMode.value;
    refreshPreview();
  });

  targetValue.addEventListener("input", () => {
    state.target = targetValue.value;
    refreshPreview();
  });

  saveButton.addEventListener("click", async () => {
    message.hidden = true;
    if (currentPreviewPayload === null || preview.hasAttribute("hidden")) {
      message.textContent = "Create a valid chart preview before saving.";
      message.hidden = false;
      return;
    }
    const title = titleInput.value.trim();
    if (title === "") {
      message.textContent = "Enter a visualization title before saving.";
      message.hidden = false;
      titleInput.focus();
      return;
    }
    const parsedTarget = Number(state.target);
    saveButton.disabled = true;
    saveButton.textContent = visualizationId === null ? "Saving…" : "Updating…";
    let png;
    try {
      png = await rasterizePreview();
    } catch (_error) {
      message.textContent = "The chart image could not be prepared for report export.";
      message.hidden = false;
      saveButton.disabled = false;
      saveButton.textContent = visualizationId === null
        ? "Save visualization"
        : "Update visualization";
      return;
    }
    const payload = {
      visualization_id: visualizationId,
      title,
      chart: state.chart,
      fields: {
        x: state.x?.name ?? null,
        y: state.y?.name ?? null,
        series: state.series?.name ?? null,
        size: state.size?.name ?? null,
        secondary_y: state.secondary_y?.name ?? null,
      },
      settings: {
        pareto_line: state.paretoLine,
        target: state.target !== "" && Number.isFinite(parsedTarget) ? parsedTarget : null,
      },
      svg: preview.outerHTML,
      png,
    };
    try {
      const response = await fetch(saveUrl, {
        method: "POST",
        headers: {"Content-Type": "application/json", Accept: "application/json"},
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error ?? "Visualization could not be saved.");
      }
      visualizationId = result.visualization_id;
      window.location.assign(result.url);
    } catch (error) {
      message.textContent = error.message;
      message.hidden = false;
      saveButton.disabled = false;
      saveButton.textContent = visualizationId === null
        ? "Save visualization"
        : "Update visualization";
    }
  });

  function rasterizePreview() {
    return new Promise((resolve, reject) => {
      const markup = new XMLSerializer().serializeToString(preview);
      const blob = new Blob([markup], {type: "image/svg+xml;charset=utf-8"});
      const objectUrl = URL.createObjectURL(blob);
      const image = new Image();
      image.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = 800;
          canvas.height = 460;
          const context = canvas.getContext("2d");
          if (context === null) {
            throw new Error("Canvas is unavailable.");
          }
          context.fillStyle = "#ffffff";
          context.fillRect(0, 0, canvas.width, canvas.height);
          context.drawImage(image, 0, 0, canvas.width, canvas.height);
          resolve(canvas.toDataURL("image/png"));
        } catch (error) {
          reject(error);
        } finally {
          URL.revokeObjectURL(objectUrl);
        }
      };
      image.onerror = () => {
        URL.revokeObjectURL(objectUrl);
        reject(new Error("Chart rasterization failed."));
      };
      image.src = objectUrl;
    });
  }

  function updateChartSettingsVisibility() {
    const combo = state.chart === "combo";
    const pareto = state.chart === "pareto";
    const targetChart = state.chart === "gauge" || state.chart === "bullet";
    secondaryYFieldGroup.hidden = !combo;
    secondaryYAxisWell.hidden = !combo;
    paretoLineSetting.hidden = !pareto;
    targetSetting.hidden = !targetChart;
    noChartSettings.hidden = combo || pareto || targetChart;
  }

  async function refreshPreview() {
    previewRequest?.abort();
    previewRequest = null;
    currentPreviewPayload = null;
    message.hidden = true;
    metadata.textContent = "";
    if (state.x === null && state.y === null) {
      emptyState.hidden = false;
      preview.setAttribute("hidden", "");
      status.textContent = chartLabels[state.chart] ?? "Visualization";
      return;
    }

    emptyState.hidden = true;
    preview.setAttribute("hidden", "");
    status.textContent = "Building preview…";
    const controller = new AbortController();
    previewRequest = controller;
    const url = new URL(previewUrl, window.location.origin);
    url.searchParams.set("chart", state.chart);
    if (state.x !== null) {
      url.searchParams.set("x", state.x.name);
    }
    if (state.y !== null) {
      url.searchParams.set("y", state.y.name);
    }
    if (state.series !== null) {
      url.searchParams.set("series", state.series.name);
    }
    if (state.size !== null) {
      url.searchParams.set("size", state.size.name);
    }
    if (state.secondary_y !== null) {
      url.searchParams.set("secondary_y", state.secondary_y.name);
    }
    if (state.target !== "") {
      url.searchParams.set("target", state.target);
    }

    try {
      const response = await fetch(url, {headers: {Accept: "application/json"}, signal: controller.signal});
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error ?? "The selected fields could not be visualized.");
      }
      renderChart(payload);
      currentPreviewPayload = payload;
      preview.removeAttribute("hidden");
      const effectiveLabel = chartLabels[payload.chart_type] ?? "Visualization";
      status.textContent = state.chart === "auto" ? `Automatic · ${effectiveLabel}` : effectiveLabel;
      metadata.textContent = `${payload.aggregation} · ${payload.record_count} record(s)${payload.truncated ? " · Preview bounded" : ""}`;
    } catch (error) {
      if (error.name === "AbortError") {
        return;
      }
      message.textContent = error.message;
      message.hidden = false;
      status.textContent = "Needs another field";
    } finally {
      if (previewRequest === controller) {
        previewRequest = null;
      }
    }
  }

  const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

  function svgElement(name, attributes = {}) {
    const element = document.createElementNS(SVG_NAMESPACE, name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
    return element;
  }

  function renderChart(payload) {
    preview.replaceChildren();
    const title = svgElement("title", {id: "preview-title"});
    title.textContent = `${chartLabels[payload.chart_type] ?? "Chart"} preview`;
    const description = svgElement("desc", {id: "preview-description"});
    description.textContent = payload.secondary_y_label
      ? `${payload.y_label} columns and ${payload.secondary_y_label} line by ${payload.x_label}`
      : payload.size_label
      ? `${payload.y_label} by ${payload.x_label}, sized by ${payload.size_label}`
      : payload.series_label
      ? `${payload.y_label} by ${payload.x_label}, split by ${payload.series_label}`
      : `${payload.y_label} by ${payload.x_label}`;
    preview.append(title, description);
    if (payload.chart_type === "heatmap") {
      renderHeatmap(payload);
    } else if (payload.chart_type === "multi_line") {
      renderMultiLine(payload);
    } else if (payload.chart_type === "stacked_100_column") {
      renderNormalizedStack(payload, false);
    } else if (payload.chart_type === "stacked_100_bar") {
      renderNormalizedStack(payload, true);
    } else if (payload.chart_type === "grouped_column") {
      renderGroupedColumns(payload);
    } else if (payload.chart_type === "stacked_column") {
      renderStackedColumns(payload, false);
    } else if (payload.chart_type === "stacked_bar") {
      renderStackedColumns(payload, true);
    } else if (payload.chart_type === "bubble") {
      renderBubble(payload);
    } else if (payload.chart_type === "scatter") {
      renderScatter(payload);
    } else if (payload.chart_type === "pie") {
      renderDonut(payload, false);
    } else if (payload.chart_type === "donut") {
      renderDonut(payload, true);
    } else if (payload.chart_type === "card") {
      renderCard(payload);
    } else if (payload.chart_type === "table") {
      renderTable(payload);
    } else if (payload.chart_type === "combo") {
      renderCombo(payload);
    } else if (payload.chart_type === "radar") {
      renderRadar(payload);
    } else if (payload.chart_type === "gauge") {
      renderGauge(payload);
    } else if (payload.chart_type === "bullet") {
      renderBullet(payload);
    } else if (payload.chart_type === "pareto") {
      renderPareto(payload, state.paretoLine);
    } else if (payload.chart_type === "waterfall") {
      renderWaterfall(payload);
    } else if (payload.chart_type === "funnel") {
      renderFunnel(payload);
    } else if (payload.chart_type === "treemap") {
      renderTreemap(payload);
    } else if (payload.chart_type === "box") {
      renderBox(payload);
    } else if (payload.chart_type === "bar") {
      renderHorizontalBars(payload);
    } else if (payload.chart_type === "line" || payload.chart_type === "area") {
      renderLine(payload, payload.chart_type === "area");
    } else {
      renderColumns(payload);
    }
  }

  function chartFrame(payload) {
    const frame = {left: 78, right: 28, top: 32, bottom: 82, width: 694, height: 346};
    const values = payload.points.map((point) => Number(point.y));
    const minimum = Math.min(0, ...values);
    const maximum = Math.max(0, ...values);
    const span = maximum - minimum || 1;
    const y = (value) => frame.top + ((maximum - value) / span) * frame.height;
    preview.append(
      svgElement("line", {x1: frame.left, y1: frame.top, x2: frame.left, y2: frame.top + frame.height, stroke: "#94a3b8"}),
      svgElement("line", {x1: frame.left, y1: y(0), x2: frame.left + frame.width, y2: y(0), stroke: "#94a3b8"})
    );
    axisLabel(payload.y_label, 20, frame.top + frame.height / 2, -90);
    axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
    return {...frame, minimum, maximum, y};
  }

  function renderColumns(payload) {
    const frame = chartFrame(payload);
    const slot = frame.width / Math.max(payload.points.length, 1);
    const width = Math.max(4, slot * 0.68);
    const zero = frame.y(0);
    payload.points.forEach((point, index) => {
      const valueY = frame.y(Number(point.y));
      const x = frame.left + index * slot + (slot - width) / 2;
      preview.append(svgElement("rect", {
        x,
        y: Math.min(valueY, zero),
        width,
        height: Math.max(1, Math.abs(zero - valueY)),
        rx: 2,
        fill: "#3b82f6",
      }));
      categoryLabel(
        point.x,
        x + width / 2,
        frame.top + frame.height + 18,
        payload.points.length,
        index
      );
    });
  }

  function renderHorizontalBars(payload) {
    const frame = {left: 140, right: 42, top: 28, bottom: 55, width: 618, height: 377};
    const values = payload.points.map((point) => Number(point.y));
    const maximum = Math.max(0, ...values) || 1;
    const minimum = Math.min(0, ...values);
    const span = maximum - minimum || 1;
    const x = (value) => frame.left + ((value - minimum) / span) * frame.width;
    const slot = frame.height / Math.max(payload.points.length, 1);
    const height = Math.max(4, slot * 0.65);
    const zero = x(0);
    preview.append(svgElement("line", {x1: zero, y1: frame.top, x2: zero, y2: frame.top + frame.height, stroke: "#94a3b8"}));
    payload.points.forEach((point, index) => {
      const valueX = x(Number(point.y));
      const y = frame.top + index * slot + (slot - height) / 2;
      preview.append(svgElement("rect", {
        x: Math.min(valueX, zero), y, width: Math.max(1, Math.abs(valueX - zero)), height, rx: 2, fill: "#3b82f6",
      }));
      const label = svgElement("text", {x: frame.left - 10, y: y + height / 2 + 4, "text-anchor": "end", fill: "#475569", "font-size": 11});
      label.textContent = shortened(point.x, 17);
      preview.append(label);
    });
    axisLabel(payload.x_label, 24, frame.top + frame.height / 2, -90);
    axisLabel(payload.y_label, frame.left + frame.width / 2, 446, 0);
  }

  function renderStackedColumns(payload, horizontal) {
    const xValues = Array.from(new Set(payload.points.map((point) => String(point.x))));
    const seriesValues = Array.from(
      new Set(payload.points.map((point) => String(point.series)))
    );
    const colors = [
      "#2563eb", "#7c3aed", "#0f766e", "#ea580c",
      "#db2777", "#0891b2", "#65a30d", "#9333ea",
    ];
    const lookup = new Map(
      payload.points.map((point) => [`${point.x}\u0000${point.series}`, Number(point.y)])
    );
    const totals = xValues.map((xValue) => seriesValues.reduce(
      (sum, series) => sum + (lookup.get(`${xValue}\u0000${series}`) ?? 0),
      0
    ));
    const maximum = Math.max(...totals, 1);
    if (horizontal) {
      const frame = {left: 145, top: 30, width: 520, height: 350};
      const slot = frame.height / xValues.length;
      const height = Math.max(5, slot * 0.65);
      xValues.forEach((xValue, xIndex) => {
        let offset = frame.left;
        seriesValues.forEach((series, seriesIndex) => {
          const value = lookup.get(`${xValue}\u0000${series}`) ?? 0;
          const width = (value / maximum) * frame.width;
          preview.append(svgElement("rect", {
            x: offset,
            y: frame.top + xIndex * slot + (slot - height) / 2,
            width: Math.max(0, width),
            height,
            fill: colors[seriesIndex % colors.length],
          }));
          offset += width;
        });
        const label = svgElement("text", {
          x: frame.left - 10,
          y: frame.top + xIndex * slot + slot / 2 + 4,
          "text-anchor": "end",
          fill: "#475569",
          "font-size": 11,
        });
        label.textContent = shortened(xValue, 17);
        preview.append(label);
      });
      axisLabel(payload.y_label, frame.left + frame.width / 2, 420, 0);
    } else {
      const frame = {left: 78, top: 32, width: 590, height: 340};
      const slot = frame.width / xValues.length;
      const width = Math.max(5, slot * 0.66);
      xValues.forEach((xValue, xIndex) => {
        let offset = frame.top + frame.height;
        seriesValues.forEach((series, seriesIndex) => {
          const value = lookup.get(`${xValue}\u0000${series}`) ?? 0;
          const height = (value / maximum) * frame.height;
          offset -= height;
          preview.append(svgElement("rect", {
            x: frame.left + xIndex * slot + (slot - width) / 2,
            y: offset,
            width,
            height: Math.max(0, height),
            fill: colors[seriesIndex % colors.length],
          }));
        });
        categoryLabel(
          xValue,
          frame.left + xIndex * slot + slot / 2,
          frame.top + frame.height + 18,
          xValues.length,
          xIndex
        );
      });
      preview.append(
        svgElement("line", {
          x1: frame.left,
          y1: frame.top + frame.height,
          x2: frame.left + frame.width,
          y2: frame.top + frame.height,
          stroke: "#94a3b8",
        })
      );
      axisLabel(payload.y_label, 20, frame.top + frame.height / 2, -90);
      axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
    }
    renderLegend(seriesValues, colors, 680, 60);
  }

  function renderGroupedColumns(payload) {
    const xValues = Array.from(new Set(payload.points.map((point) => String(point.x))));
    const seriesValues = Array.from(
      new Set(payload.points.map((point) => String(point.series)))
    );
    const colors = [
      "#2563eb", "#7c3aed", "#0f766e", "#ea580c",
      "#db2777", "#0891b2", "#65a30d", "#9333ea",
    ];
    const lookup = new Map(
      payload.points.map((point) => [`${point.x}\u0000${point.series}`, Number(point.y)])
    );
    const values = payload.points.map((point) => Number(point.y));
    const minimum = Math.min(0, ...values);
    const maximum = Math.max(0, ...values);
    const span = maximum - minimum || 1;
    const frame = {left: 78, top: 32, width: 585, height: 340};
    const y = (value) => frame.top + ((maximum - value) / span) * frame.height;
    const slot = frame.width / xValues.length;
    const groupWidth = slot * 0.76;
    const barWidth = Math.max(2, groupWidth / seriesValues.length);
    const zero = y(0);
    xValues.forEach((xValue, xIndex) => {
      seriesValues.forEach((series, seriesIndex) => {
        const value = lookup.get(`${xValue}\u0000${series}`) ?? 0;
        const valueY = y(value);
        preview.append(svgElement("rect", {
          x: frame.left + xIndex * slot + (slot - groupWidth) / 2 + seriesIndex * barWidth,
          y: Math.min(valueY, zero),
          width: Math.max(1, barWidth - 1),
          height: Math.max(1, Math.abs(zero - valueY)),
          fill: colors[seriesIndex % colors.length],
        }));
      });
      categoryLabel(
        xValue,
        frame.left + xIndex * slot + slot / 2,
        frame.top + frame.height + 18,
        xValues.length,
        xIndex
      );
    });
    preview.append(svgElement("line", {
      x1: frame.left,
      y1: zero,
      x2: frame.left + frame.width,
      y2: zero,
      stroke: "#94a3b8",
    }));
    axisLabel(payload.y_label, 20, frame.top + frame.height / 2, -90);
    axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
    renderLegend(seriesValues, colors, 680, 60);
  }

  function renderNormalizedStack(payload, horizontal) {
    const xValues = Array.from(new Set(payload.points.map((point) => String(point.x))));
    const seriesValues = Array.from(
      new Set(payload.points.map((point) => String(point.series)))
    );
    const colors = [
      "#2563eb", "#7c3aed", "#0f766e", "#ea580c",
      "#db2777", "#0891b2", "#65a30d", "#9333ea",
    ];
    const lookup = new Map(
      payload.points.map((point) => [`${point.x}\u0000${point.series}`, Number(point.y)])
    );
    const totals = new Map(xValues.map((xValue) => [
      xValue,
      seriesValues.reduce(
        (sum, series) => sum + (lookup.get(`${xValue}\u0000${series}`) ?? 0),
        0
      ),
    ]));
    if (horizontal) {
      const frame = {left: 145, top: 30, width: 520, height: 350};
      const slot = frame.height / xValues.length;
      const height = Math.max(5, slot * 0.65);
      xValues.forEach((xValue, xIndex) => {
        let offset = frame.left;
        seriesValues.forEach((series, seriesIndex) => {
          const proportion = (
            (lookup.get(`${xValue}\u0000${series}`) ?? 0) /
            (totals.get(xValue) || 1)
          );
          const width = proportion * frame.width;
          preview.append(svgElement("rect", {
            x: offset,
            y: frame.top + xIndex * slot + (slot - height) / 2,
            width: Math.max(0, width),
            height,
            fill: colors[seriesIndex % colors.length],
          }));
          offset += width;
        });
        const label = svgElement("text", {
          x: frame.left - 10,
          y: frame.top + xIndex * slot + slot / 2 + 4,
          "text-anchor": "end",
          fill: "#475569",
          "font-size": 11,
        });
        label.textContent = shortened(xValue, 17);
        preview.append(label);
      });
      axisLabel("Percentage", frame.left + frame.width / 2, 420, 0);
    } else {
      const frame = {left: 78, top: 32, width: 585, height: 340};
      const slot = frame.width / xValues.length;
      const width = Math.max(5, slot * 0.66);
      xValues.forEach((xValue, xIndex) => {
        let offset = frame.top + frame.height;
        seriesValues.forEach((series, seriesIndex) => {
          const proportion = (
            (lookup.get(`${xValue}\u0000${series}`) ?? 0) /
            (totals.get(xValue) || 1)
          );
          const height = proportion * frame.height;
          offset -= height;
          preview.append(svgElement("rect", {
            x: frame.left + xIndex * slot + (slot - width) / 2,
            y: offset,
            width,
            height: Math.max(0, height),
            fill: colors[seriesIndex % colors.length],
          }));
        });
        categoryLabel(
          xValue,
          frame.left + xIndex * slot + slot / 2,
          frame.top + frame.height + 18,
          xValues.length,
          xIndex
        );
      });
      axisLabel("Percentage", 20, frame.top + frame.height / 2, -90);
      axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
    }
    renderLegend(seriesValues, colors, 680, 60);
  }

  function renderMultiLine(payload) {
    const xValues = Array.from(new Set(payload.points.map((point) => String(point.x))));
    const seriesValues = Array.from(
      new Set(payload.points.map((point) => String(point.series)))
    );
    const colors = [
      "#2563eb", "#7c3aed", "#0f766e", "#ea580c",
      "#db2777", "#0891b2", "#65a30d", "#9333ea",
    ];
    const lookup = new Map(
      payload.points.map((point) => [`${point.x}\u0000${point.series}`, Number(point.y)])
    );
    const values = payload.points.map((point) => Number(point.y));
    const minimum = Math.min(0, ...values);
    const maximum = Math.max(0, ...values);
    const span = maximum - minimum || 1;
    const frame = {left: 78, top: 32, width: 585, height: 340};
    const y = (value) => frame.top + ((maximum - value) / span) * frame.height;
    const step = frame.width / Math.max(xValues.length - 1, 1);
    seriesValues.forEach((series, seriesIndex) => {
      const coordinates = xValues.map((xValue, index) => [
        frame.left + index * step,
        y(lookup.get(`${xValue}\u0000${series}`) ?? 0),
      ]);
      preview.append(svgElement("polyline", {
        points: coordinates.map((point) => point.join(",")).join(" "),
        fill: "none",
        stroke: colors[seriesIndex % colors.length],
        "stroke-width": 2.5,
      }));
      coordinates.forEach(([x, pointY]) => preview.append(svgElement("circle", {
        cx: x,
        cy: pointY,
        r: 3,
        fill: colors[seriesIndex % colors.length],
      })));
    });
    xValues.forEach((xValue, index) => categoryLabel(
      xValue,
      frame.left + index * step,
      frame.top + frame.height + 18,
      xValues.length,
      index
    ));
    axisLabel(payload.y_label, 20, frame.top + frame.height / 2, -90);
    axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
    renderLegend(seriesValues, colors, 680, 60);
  }

  function renderLine(payload, fillArea) {
    const frame = chartFrame(payload);
    const step = frame.width / Math.max(payload.points.length - 1, 1);
    const coordinates = payload.points.map((point, index) => [frame.left + index * step, frame.y(Number(point.y))]);
    if (fillArea) {
      const baseline = frame.y(0);
      const areaPoints = [[coordinates[0][0], baseline], ...coordinates, [coordinates.at(-1)[0], baseline]];
      preview.append(svgElement("polygon", {points: areaPoints.map((point) => point.join(",")).join(" "), fill: "#bfdbfe", opacity: 0.8}));
    }
    preview.append(svgElement("polyline", {points: coordinates.map((point) => point.join(",")).join(" "), fill: "none", stroke: "#2563eb", "stroke-width": 3, "stroke-linejoin": "round"}));
    coordinates.forEach(([x, y], index) => {
      preview.append(svgElement("circle", {cx: x, cy: y, r: 4, fill: "#fff", stroke: "#2563eb", "stroke-width": 2}));
      categoryLabel(
        payload.points[index].x,
        x,
        frame.top + frame.height + 18,
        payload.points.length,
        index
      );
    });
  }

  function renderCombo(payload) {
    const frame = {left: 78, top: 32, width: 680, height: 340};
    const values = payload.points.flatMap((point) => [
      Number(point.y),
      Number(point.secondary_y),
    ]);
    const minimum = Math.min(0, ...values);
    const maximum = Math.max(0, ...values);
    const span = maximum - minimum || 1;
    const y = (value) => frame.top + ((maximum - value) / span) * frame.height;
    const slot = frame.width / payload.points.length;
    const width = Math.max(5, slot * 0.55);
    const zero = y(0);
    const linePoints = [];
    payload.points.forEach((point, index) => {
      const center = frame.left + index * slot + slot / 2;
      const valueY = y(Number(point.y));
      preview.append(svgElement("rect", {
        x: center - width / 2,
        y: Math.min(valueY, zero),
        width,
        height: Math.max(1, Math.abs(zero - valueY)),
        fill: "#60a5fa",
      }));
      linePoints.push([center, y(Number(point.secondary_y))]);
      categoryLabel(
        point.x,
        center,
        frame.top + frame.height + 18,
        payload.points.length,
        index
      );
    });
    preview.append(svgElement("polyline", {
      points: linePoints.map((point) => point.join(",")).join(" "),
      fill: "none",
      stroke: "#ea580c",
      "stroke-width": 3,
    }));
    linePoints.forEach(([x, pointY]) => preview.append(svgElement("circle", {
      cx: x,
      cy: pointY,
      r: 4,
      fill: "#fff",
      stroke: "#ea580c",
      "stroke-width": 2,
    })));
    axisLabel(payload.y_label, 20, frame.top + frame.height / 2, -90);
    axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
    renderLegend(
      [payload.y_label, payload.secondary_y_label],
      ["#60a5fa", "#ea580c"],
      635,
      55
    );
  }

  function renderScatter(payload) {
    const frame = {left: 78, right: 28, top: 32, bottom: 66, width: 694, height: 362};
    const xValues = payload.points.map((point) => Number(point.x));
    const yValues = payload.points.map((point) => Number(point.y));
    const xMin = Math.min(...xValues);
    const xMax = Math.max(...xValues);
    const yMin = Math.min(...yValues);
    const yMax = Math.max(...yValues);
    const x = (value) => frame.left + ((value - xMin) / (xMax - xMin || 1)) * frame.width;
    const y = (value) => frame.top + ((yMax - value) / (yMax - yMin || 1)) * frame.height;
    preview.append(
      svgElement("line", {x1: frame.left, y1: frame.top, x2: frame.left, y2: frame.top + frame.height, stroke: "#94a3b8"}),
      svgElement("line", {x1: frame.left, y1: frame.top + frame.height, x2: frame.left + frame.width, y2: frame.top + frame.height, stroke: "#94a3b8"})
    );
    payload.points.forEach((point) => preview.append(svgElement("circle", {cx: x(Number(point.x)), cy: y(Number(point.y)), r: 4, fill: "#2563eb", opacity: 0.72})));
    axisLabel(payload.y_label, 20, frame.top + frame.height / 2, -90);
    axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
  }

  function renderBubble(payload) {
    const frame = {left: 78, right: 28, top: 32, bottom: 66, width: 694, height: 362};
    const xValues = payload.points.map((point) => Number(point.x));
    const yValues = payload.points.map((point) => Number(point.y));
    const sizeValues = payload.points.map((point) => Number(point.size));
    const xMin = Math.min(...xValues);
    const xMax = Math.max(...xValues);
    const yMin = Math.min(...yValues);
    const yMax = Math.max(...yValues);
    const sizeMin = Math.min(...sizeValues);
    const sizeMax = Math.max(...sizeValues);
    const x = (value) => frame.left + ((value - xMin) / (xMax - xMin || 1)) * frame.width;
    const y = (value) => frame.top + ((yMax - value) / (yMax - yMin || 1)) * frame.height;
    const radius = (value) => {
      if (sizeMax === sizeMin) {
        return 12;
      }
      const normalized = (value - sizeMin) / (sizeMax - sizeMin);
      return 5 + Math.sqrt(normalized) * 18;
    };
    preview.append(
      svgElement("line", {x1: frame.left, y1: frame.top, x2: frame.left, y2: frame.top + frame.height, stroke: "#94a3b8"}),
      svgElement("line", {x1: frame.left, y1: frame.top + frame.height, x2: frame.left + frame.width, y2: frame.top + frame.height, stroke: "#94a3b8"})
    );
    payload.points.forEach((point) => preview.append(svgElement("circle", {
      cx: x(Number(point.x)),
      cy: y(Number(point.y)),
      r: radius(Number(point.size)),
      fill: "#2563eb",
      opacity: 0.55,
      stroke: "#1d4ed8",
      "stroke-width": 1.5,
    })));
    axisLabel(payload.y_label, 20, frame.top + frame.height / 2, -90);
    axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
    const sizeLabel = svgElement("text", {
      x: 765,
      y: 25,
      "text-anchor": "end",
      fill: "#64748b",
      "font-size": 10,
    });
    sizeLabel.textContent = `Bubble size: ${payload.size_label}`;
    preview.append(sizeLabel);
  }

  function renderDonut(payload, hasHole) {
    const centerX = 400;
    const centerY = 220;
    const radius = 130;
    const innerRadius = hasHole ? 72 : 0;
    const total = payload.points.reduce((sum, point) => sum + Number(point.y), 0);
    const colors = ["#2563eb", "#7c3aed", "#0f766e", "#ea580c", "#db2777", "#0891b2", "#65a30d", "#9333ea"];
    let angle = -Math.PI / 2;
    payload.points.forEach((point, index) => {
      const nextAngle = angle + (Number(point.y) / total) * Math.PI * 2;
      preview.append(svgElement("path", {d: donutPath(centerX, centerY, radius, innerRadius, angle, nextAngle), fill: colors[index % colors.length], stroke: "#fff", "stroke-width": 2}));
      const legend = svgElement("text", {x: 570, y: 105 + index * 28, fill: "#475569", "font-size": 12});
      legend.textContent = `${shortened(point.x, 17)} · ${formatNumber(point.y)}`;
      preview.append(svgElement("rect", {x: 545, y: 94 + index * 28, width: 14, height: 14, rx: 2, fill: colors[index % colors.length]}), legend);
      angle = nextAngle;
    });
    if (hasHole) {
      const totalLabel = svgElement("text", {x: centerX, y: centerY + 4, "text-anchor": "middle", fill: "#172033", "font-size": 22, "font-weight": 750});
      totalLabel.textContent = formatNumber(total);
      preview.append(totalLabel);
    }
  }

  function renderCard(payload) {
    const point = payload.points[0];
    const value = svgElement("text", {
      x: 400,
      y: 225,
      "text-anchor": "middle",
      fill: "#172033",
      "font-size": 64,
      "font-weight": 800,
    });
    value.textContent = formatNumber(point.y);
    const label = svgElement("text", {
      x: 400,
      y: 275,
      "text-anchor": "middle",
      fill: "#64748b",
      "font-size": 20,
      "font-weight": 650,
    });
    label.textContent = payload.y_label;
    preview.append(value, label);
  }

  function renderRadar(payload) {
    const points = payload.points;
    const centerX = 400;
    const centerY = 220;
    const radius = 150;
    const maximum = Math.max(...points.map((point) => Number(point.y)), 1);
    const polygon = [];
    for (let level = 1; level <= 4; level += 1) {
      const levelPoints = points.map((_point, index) => {
        const angle = -Math.PI / 2 + index * Math.PI * 2 / points.length;
        return polar(centerX, centerY, radius * level / 4, angle);
      });
      preview.append(svgElement("polygon", {
        points: levelPoints.map((point) => `${point.x},${point.y}`).join(" "),
        fill: "none",
        stroke: "#cbd5e1",
      }));
    }
    points.forEach((point, index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / points.length;
      const outer = polar(centerX, centerY, radius, angle);
      const valuePoint = polar(
        centerX,
        centerY,
        radius * Number(point.y) / maximum,
        angle
      );
      polygon.push(valuePoint);
      preview.append(svgElement("line", {
        x1: centerX,
        y1: centerY,
        x2: outer.x,
        y2: outer.y,
        stroke: "#cbd5e1",
      }));
      const labelPoint = polar(centerX, centerY, radius + 25, angle);
      const label = svgElement("text", {
        x: labelPoint.x,
        y: labelPoint.y,
        "text-anchor": "middle",
        fill: "#475569",
        "font-size": 10,
      });
      label.textContent = shortened(point.x, 14);
      preview.append(label);
    });
    preview.append(svgElement("polygon", {
      points: polygon.map((point) => `${point.x},${point.y}`).join(" "),
      fill: "#60a5fa",
      opacity: 0.42,
      stroke: "#2563eb",
      "stroke-width": 2.5,
    }));
  }

  function renderGauge(payload) {
    const actual = Number(payload.points[0].y);
    const target = Number(payload.target);
    const percentage = actual / target;
    const centerX = 400;
    const centerY = 285;
    const radius = 150;
    preview.append(svgElement("path", {
      d: arcPath(centerX, centerY, radius, Math.PI, Math.PI * 2),
      fill: "none",
      stroke: "#e2e8f0",
      "stroke-width": 30,
      "stroke-linecap": "round",
    }));
    preview.append(svgElement("path", {
      d: arcPath(
        centerX,
        centerY,
        radius,
        Math.PI,
        Math.PI + Math.min(Math.max(percentage, 0), 1) * Math.PI
      ),
      fill: "none",
      stroke: percentage >= 1 ? "#0f766e" : "#2563eb",
      "stroke-width": 30,
      "stroke-linecap": "round",
    }));
    const value = svgElement("text", {
      x: centerX,
      y: centerY - 35,
      "text-anchor": "middle",
      fill: "#172033",
      "font-size": 44,
      "font-weight": 800,
    });
    value.textContent = formatNumber(actual);
    const targetLabel = svgElement("text", {
      x: centerX,
      y: centerY + 5,
      "text-anchor": "middle",
      fill: "#64748b",
      "font-size": 14,
    });
    targetLabel.textContent = `Target ${formatNumber(target)} · ${formatNumber(percentage * 100)}%`;
    preview.append(value, targetLabel);
  }

  function renderBullet(payload) {
    const actual = Number(payload.points[0].y);
    const target = Number(payload.target);
    const maximum = Math.max(actual, target) * 1.15 || 1;
    const frame = {left: 120, top: 175, width: 560, height: 90};
    preview.append(
      svgElement("rect", {x: frame.left, y: frame.top, width: frame.width, height: frame.height, rx: 5, fill: "#e2e8f0"}),
      svgElement("rect", {x: frame.left, y: frame.top + 22, width: frame.width * actual / maximum, height: 46, rx: 3, fill: actual >= target ? "#0f766e" : "#2563eb"}),
      svgElement("line", {x1: frame.left + frame.width * target / maximum, y1: frame.top - 10, x2: frame.left + frame.width * target / maximum, y2: frame.top + frame.height + 10, stroke: "#172033", "stroke-width": 4})
    );
    const label = svgElement("text", {x: frame.left, y: frame.top - 35, fill: "#172033", "font-size": 22, "font-weight": 750});
    label.textContent = `${payload.y_label}: ${formatNumber(actual)}`;
    const targetLabel = svgElement("text", {x: frame.left + frame.width, y: frame.top + frame.height + 38, "text-anchor": "end", fill: "#64748b", "font-size": 13});
    targetLabel.textContent = `Target ${formatNumber(target)}`;
    preview.append(label, targetLabel);
  }

  function renderTable(payload) {
    const rows = payload.points.slice(0, 12);
    const left = 110;
    const top = 55;
    const width = 580;
    const rowHeight = 28;
    preview.append(svgElement("rect", {
      x: left,
      y: top,
      width,
      height: rowHeight,
      fill: "#dbeafe",
    }));
    tableText(payload.x_label, left + 12, top + 19, "start", true);
    tableText(payload.y_label, left + width - 12, top + 19, "end", true);
    rows.forEach((point, index) => {
      const y = top + rowHeight * (index + 1);
      preview.append(svgElement("line", {
        x1: left,
        y1: y + rowHeight,
        x2: left + width,
        y2: y + rowHeight,
        stroke: "#e2e8f0",
      }));
      tableText(shortened(point.x, 36), left + 12, y + 19, "start", false);
      tableText(formatNumber(point.y), left + width - 12, y + 19, "end", false);
    });
  }

  function renderPareto(payload, lineMode) {
    const frame = chartFrame(payload);
    const total = payload.points.reduce((sum, point) => sum + Number(point.y), 0);
    const slot = frame.width / payload.points.length;
    const width = Math.max(5, slot * 0.62);
    let running = 0;
    const linePoints = [];
    payload.points.forEach((point, index) => {
      const value = Number(point.y);
      const valueY = frame.y(value);
      const x = frame.left + index * slot + (slot - width) / 2;
      preview.append(svgElement("rect", {
        x,
        y: valueY,
        width,
        height: frame.y(0) - valueY,
        rx: 2,
        fill: "#3b82f6",
      }));
      running += value;
      if (lineMode !== "none") {
        const lineValue = lineMode === "cumulative_value"
          ? running
          : lineMode === "individual_percent"
          ? (value / total) * 100
          : (running / total) * 100;
        const lineMaximum = lineMode === "cumulative_value" ? total : 100;
        const lineY = frame.top + frame.height - (lineValue / lineMaximum) * frame.height;
        linePoints.push([x + width / 2, lineY]);
      }
      categoryLabel(
        point.x,
        x + width / 2,
        frame.top + frame.height + 18,
        payload.points.length,
        index
      );
    });
    if (lineMode !== "none") {
      preview.append(svgElement("polyline", {
        points: linePoints.map((point) => point.join(",")).join(" "),
        fill: "none",
        stroke: "#ea580c",
        "stroke-width": 3,
      }));
      linePoints.forEach(([x, y]) => preview.append(svgElement("circle", {
        cx: x,
        cy: y,
        r: 4,
        fill: "#fff",
        stroke: "#ea580c",
        "stroke-width": 2,
      })));
      const lineLabel = svgElement("text", {
        x: 770,
        y: frame.top + 8,
        "text-anchor": "end",
        fill: "#ea580c",
        "font-size": 11,
      });
      lineLabel.textContent = {
        cumulative_percent: "Cumulative percentage",
        cumulative_value: "Cumulative value",
        individual_percent: "Individual percentage",
      }[lineMode] ?? "Pareto line";
      preview.append(lineLabel);
    }
  }

  function renderWaterfall(payload) {
    const frame = {left: 78, top: 32, width: 680, height: 340};
    const cumulative = [0];
    payload.points.forEach((point) => {
      cumulative.push(cumulative.at(-1) + Number(point.y));
    });
    const minimum = Math.min(0, ...cumulative);
    const maximum = Math.max(0, ...cumulative);
    const span = maximum - minimum || 1;
    const y = (value) => frame.top + ((maximum - value) / span) * frame.height;
    const slot = frame.width / payload.points.length;
    const width = Math.max(5, slot * 0.58);
    payload.points.forEach((point, index) => {
      const start = cumulative[index];
      const end = cumulative[index + 1];
      const x = frame.left + index * slot + (slot - width) / 2;
      preview.append(svgElement("rect", {
        x,
        y: Math.min(y(start), y(end)),
        width,
        height: Math.max(2, Math.abs(y(start) - y(end))),
        fill: end >= start ? "#0f766e" : "#dc2626",
      }));
      if (index < payload.points.length - 1) {
        preview.append(svgElement("line", {
          x1: x + width,
          y1: y(end),
          x2: frame.left + (index + 1) * slot + (slot - width) / 2,
          y2: y(end),
          stroke: "#94a3b8",
          "stroke-dasharray": "3 3",
        }));
      }
      categoryLabel(
        point.x,
        x + width / 2,
        frame.top + frame.height + 18,
        payload.points.length,
        index
      );
    });
    preview.append(svgElement("line", {
      x1: frame.left,
      y1: y(0),
      x2: frame.left + frame.width,
      y2: y(0),
      stroke: "#94a3b8",
    }));
    axisLabel(payload.y_label, 20, frame.top + frame.height / 2, -90);
    axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
  }

  function renderFunnel(payload) {
    const maximum = Math.max(...payload.points.map((point) => Number(point.y)), 1);
    const center = 400;
    const top = 45;
    const rowHeight = Math.min(58, 340 / payload.points.length);
    payload.points.forEach((point, index) => {
      const width = 560 * (Number(point.y) / maximum);
      const y = top + index * rowHeight;
      preview.append(svgElement("rect", {
        x: center - width / 2,
        y,
        width,
        height: rowHeight - 5,
        rx: 4,
        fill: `hsl(${215 + index * 8} 78% ${52 + index * 3}%)`,
      }));
      const label = svgElement("text", {
        x: center,
        y: y + rowHeight / 2 + 4,
        "text-anchor": "middle",
        fill: "#fff",
        "font-size": 11,
        "font-weight": 700,
      });
      label.textContent = `${shortened(point.x, 20)} · ${formatNumber(point.y)}`;
      preview.append(label);
    });
  }

  function renderTreemap(payload) {
    const items = payload.points.map((point, index) => ({
      label: String(point.x),
      value: Number(point.y),
      colorIndex: index,
    }));
    const rectangles = [];
    layoutTreemap(items, 45, 35, 710, 365, true, rectangles);
    const colors = ["#2563eb", "#7c3aed", "#0f766e", "#ea580c", "#db2777", "#0891b2", "#65a30d", "#9333ea"];
    rectangles.forEach((rectangle) => {
      preview.append(svgElement("rect", {
        x: rectangle.x + 1,
        y: rectangle.y + 1,
        width: Math.max(0, rectangle.width - 2),
        height: Math.max(0, rectangle.height - 2),
        rx: 3,
        fill: colors[rectangle.item.colorIndex % colors.length],
      }));
      if (rectangle.width > 70 && rectangle.height > 35) {
        const label = svgElement("text", {
          x: rectangle.x + 8,
          y: rectangle.y + 20,
          fill: "#fff",
          "font-size": 11,
          "font-weight": 700,
        });
        label.textContent = shortened(rectangle.item.label, 18);
        preview.append(label);
      }
    });
  }

  function layoutTreemap(items, x, y, width, height, vertical, output) {
    if (items.length === 0) {
      return;
    }
    if (items.length === 1) {
      output.push({item: items[0], x, y, width, height});
      return;
    }
    const total = items.reduce((sum, item) => sum + item.value, 0);
    let running = items[0].value;
    let splitIndex = 1;
    while (splitIndex < items.length - 1 && running < total / 2) {
      running += items[splitIndex].value;
      splitIndex += 1;
    }
    const first = items.slice(0, splitIndex);
    const second = items.slice(splitIndex);
    const ratio = first.reduce((sum, item) => sum + item.value, 0) / total;
    if (vertical) {
      layoutTreemap(first, x, y, width * ratio, height, false, output);
      layoutTreemap(second, x + width * ratio, y, width * (1 - ratio), height, false, output);
    } else {
      layoutTreemap(first, x, y, width, height * ratio, true, output);
      layoutTreemap(second, x, y + height * ratio, width, height * (1 - ratio), true, output);
    }
  }

  function renderBox(payload) {
    const frame = {left: 78, top: 32, width: 680, height: 340};
    const allValues = payload.points.flatMap((point) => [point.minimum, point.maximum]);
    const minimum = Math.min(...allValues);
    const maximum = Math.max(...allValues);
    const span = maximum - minimum || 1;
    const y = (value) => frame.top + ((maximum - value) / span) * frame.height;
    const slot = frame.width / payload.points.length;
    const boxWidth = Math.min(54, slot * 0.5);
    payload.points.forEach((point, index) => {
      const center = frame.left + index * slot + slot / 2;
      preview.append(
        svgElement("line", {x1: center, y1: y(point.minimum), x2: center, y2: y(point.maximum), stroke: "#475569", "stroke-width": 2}),
        svgElement("line", {x1: center - boxWidth / 3, y1: y(point.minimum), x2: center + boxWidth / 3, y2: y(point.minimum), stroke: "#475569", "stroke-width": 2}),
        svgElement("line", {x1: center - boxWidth / 3, y1: y(point.maximum), x2: center + boxWidth / 3, y2: y(point.maximum), stroke: "#475569", "stroke-width": 2}),
        svgElement("rect", {x: center - boxWidth / 2, y: y(point.q3), width: boxWidth, height: Math.max(2, y(point.q1) - y(point.q3)), fill: "#bfdbfe", stroke: "#2563eb", "stroke-width": 2}),
        svgElement("line", {x1: center - boxWidth / 2, y1: y(point.median), x2: center + boxWidth / 2, y2: y(point.median), stroke: "#1e3a8a", "stroke-width": 3})
      );
      categoryLabel(point.x, center, frame.top + frame.height + 18, payload.points.length, index);
    });
    axisLabel(payload.y_label, 20, frame.top + frame.height / 2, -90);
    axisLabel(payload.x_label, frame.left + frame.width / 2, 446, 0);
  }

  function renderHeatmap(payload) {
    const xValues = Array.from(new Set(payload.points.map((point) => String(point.x))));
    const seriesValues = Array.from(new Set(payload.points.map((point) => String(point.series))));
    const values = payload.points.map((point) => Number(point.y));
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const largestAbsolute = Math.max(Math.abs(minimum), Math.abs(maximum), 1);
    const lookup = new Map(
      payload.points.map((point) => [`${point.x}\u0000${point.series}`, Number(point.y)])
    );
    const frame = {left: 145, top: 45, width: 590, height: 330};
    const cellWidth = frame.width / xValues.length;
    const cellHeight = frame.height / seriesValues.length;
    seriesValues.forEach((series, rowIndex) => {
      xValues.forEach((xValue, columnIndex) => {
        const value = lookup.get(`${xValue}\u0000${series}`) ?? 0;
        const opacity = 0.15 + 0.85 * Math.abs(value) / largestAbsolute;
        preview.append(svgElement("rect", {
          x: frame.left + columnIndex * cellWidth,
          y: frame.top + rowIndex * cellHeight,
          width: cellWidth,
          height: cellHeight,
          fill: value < 0 ? `rgb(220 38 38 / ${opacity})` : `rgb(37 99 235 / ${opacity})`,
          stroke: "#fff",
        }));
      });
      const rowLabel = svgElement("text", {
        x: frame.left - 8,
        y: frame.top + rowIndex * cellHeight + cellHeight / 2 + 4,
        "text-anchor": "end",
        fill: "#475569",
        "font-size": 10,
      });
      rowLabel.textContent = shortened(series, 17);
      preview.append(rowLabel);
    });
    xValues.forEach((xValue, index) => categoryLabel(
      xValue,
      frame.left + index * cellWidth + cellWidth / 2,
      frame.top + frame.height + 18,
      xValues.length,
      index
    ));
    axisLabel(payload.series_label, 20, frame.top + frame.height / 2, -90);
    axisLabel(payload.x_label, frame.left + frame.width / 2, 430, 0);
  }

  function tableText(value, x, y, anchor, bold) {
    const text = svgElement("text", {
      x,
      y,
      "text-anchor": anchor,
      fill: "#334155",
      "font-size": 12,
      "font-weight": bold ? 750 : 500,
    });
    text.textContent = value;
    preview.append(text);
  }

  function renderLegend(values, colors, x, y) {
    values.forEach((value, index) => {
      preview.append(svgElement("rect", {
        x,
        y: y + index * 25,
        width: 12,
        height: 12,
        rx: 2,
        fill: colors[index % colors.length],
      }));
      const label = svgElement("text", {
        x: x + 18,
        y: y + 10 + index * 25,
        fill: "#475569",
        "font-size": 10,
      });
      label.textContent = shortened(value, 13);
      preview.append(label);
    });
  }

  function donutPath(cx, cy, outer, inner, start, end) {
    const outerStart = polar(cx, cy, outer, start);
    const outerEnd = polar(cx, cy, outer, end);
    const innerEnd = polar(cx, cy, inner, end);
    const innerStart = polar(cx, cy, inner, start);
    const large = end - start > Math.PI ? 1 : 0;
    if (inner === 0) {
      return `M ${cx} ${cy} L ${outerStart.x} ${outerStart.y} A ${outer} ${outer} 0 ${large} 1 ${outerEnd.x} ${outerEnd.y} Z`;
    }
    return `M ${outerStart.x} ${outerStart.y} A ${outer} ${outer} 0 ${large} 1 ${outerEnd.x} ${outerEnd.y} L ${innerEnd.x} ${innerEnd.y} A ${inner} ${inner} 0 ${large} 0 ${innerStart.x} ${innerStart.y} Z`;
  }

  function polar(cx, cy, radius, angle) {
    return {x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle)};
  }

  function arcPath(cx, cy, radius, startAngle, endAngle) {
    const start = polar(cx, cy, radius, startAngle);
    const end = polar(cx, cy, radius, endAngle);
    const large = endAngle - startAngle > Math.PI ? 1 : 0;
    return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${large} 1 ${end.x} ${end.y}`;
  }

  function categoryLabel(value, x, y, count, index) {
    const every = count > 10 ? Math.ceil(count / 8) : 1;
    if (index % every !== 0) {
      return;
    }
    const label = svgElement("text", {class: "category-label", x, y, "text-anchor": "end", fill: "#475569", "font-size": 10, transform: `rotate(-28 ${x} ${y})`});
    label.textContent = shortened(value, 14);
    preview.append(label);
  }

  function axisLabel(value, x, y, rotation) {
    const label = svgElement("text", {x, y, "text-anchor": "middle", fill: "#334155", "font-size": 12, "font-weight": 650, transform: rotation === 0 ? "" : `rotate(${rotation} ${x} ${y})`});
    label.textContent = value;
    preview.append(label);
  }

  function shortened(value, length) {
    const text = String(value);
    return text.length > length ? `${text.slice(0, length - 1)}…` : text;
  }

  function formatNumber(value) {
    return new Intl.NumberFormat(undefined, {maximumFractionDigits: 2, notation: "compact"}).format(Number(value));
  }

  updateChartSettingsVisibility();
  renderAxisWells();
  if (
    state.x !== null || state.y !== null || state.series !== null ||
    state.size !== null || state.secondary_y !== null
  ) {
    refreshPreview();
  }
})();
