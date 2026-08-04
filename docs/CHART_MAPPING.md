# Chart-to-column mapping

This document is the shared contract for the guided visualization builder,
Ollama suggestions, Python validation, chart generation, and saved-dashboard
artifacts. Ollama may recommend a mapping, but Python decides whether the
mapping is valid and calculates every plotted value.

## Semantic column types

The visualization-suggestion layer derives a bounded semantic overlay from the
deterministic dataset profile:

| Semantic type | Meaning | Typical examples |
| --- | --- | --- |
| `TEMPORAL` | Naturally ordered dates or timestamps | `Order_Date`, `Timestamp` |
| `CATEGORICAL_NOMINAL` | Unordered business groups | `Product_Category`, `Channel` |
| `CATEGORICAL_ORDINAL` | Groups whose labels imply an order | `Job_Level`, `Stage`, `Priority` |
| `NUMERIC_CONTINUOUS` | Measured numeric values | `Revenue`, `Temperature` |
| `NUMERIC_DISCRETE` | Count-like numeric values | `Units_Sold`, `Headcount` |
| `BOOLEAN_FLAG` | Two-state values | `Stockout_Flag`, `On_Time` |
| `IDENTIFIER` | Record keys, never model-selected as measures | `Order_ID`, `SKU_ID` |
| `GEOGRAPHIC` | Location-named fields | `Country`, `City`, `Region` |

Ordinal, numeric-discrete, and geographic labels are conservative semantic
hints for Ollama. They do not silently change the deterministic profiler or
authorize geographic lookup. Exact chart compatibility still uses the
profile's date, category, and numeric candidates.

## Implemented chart contracts

| Chart | Required mapping | Optional mapping | Deterministic restrictions |
| --- | --- | --- | --- |
| Line | temporal x + numeric measure | category series; compatible extra measures | Series with time supports one measure |
| Area | temporal x + numeric measure | category series; compatible extra measures | Uses aggregated period values |
| Stacked area | temporal x + summable measure | category series; summable extra measures | Aggregation must resolve to sum or count |
| Bar | category x + aggregated numeric measure | category series; compatible extra measures | Group count is bounded by Top-N |
| Horizontal bar | category x + aggregated numeric measure | category series; compatible extra measures | Useful for rankings and long labels |
| Stacked bar | category x + summable measure | category series; summable extra measures | Aggregation must resolve to sum or count |
| Pareto | category x + one summable measure | none | Non-negative values; positive total |
| Donut | nominal category + one summable measure | none | At most seven category values; non-negative positive total |
| Scatter | numeric x + one row-level numeric measure | category colour | Aggregate-only KPIs are rejected |
| Histogram | one row-level numeric measure | bin count | No grouping field |
| Box | one row-level numeric measure | category x | Aggregate-only KPIs are rejected |
| Heatmap | category x + different category series + one numeric measure | none | Both category fields are required and must differ |
| Waterfall | ordered category x + one numeric delta measure | source-order sorting | Negative and positive deltas are retained |
| Funnel | ordered stage category x + one count/summable measure | source-order sorting | Values must be non-negative with a positive total |
| Combo bar + line | category x + exactly two compatible numeric measures | none | Measures must share a display format |
| KPI scorecard | exactly one aggregated numeric measure | none | No grouping field |

Grouped bars already provide the grouped-bar behavior when multiple measures
or a category series are selected.

## Ollama suggestion boundary

`visualization_suggestions.py` sends only bounded metadata:

- column names, semantic types, uniqueness, and missing counts;
- detected date/category/numeric candidates;
- allowed measure selectors and reviewed KPI semantics;
- the user's visualization question.

It does not send raw source rows. JSON Schema restricts chart types, measure
selectors, x fields, series fields, aggregation, and date granularity.
`parse_visualization_spec()` and `validate_visualization_spec()` then apply the
same Python rules as the manual builder. Only a valid suggestion can become a
generated preview, and the user must explicitly save that preview.

## Deferred mappings requiring a richer specification

These charts are intentionally not approximated with ambiguous fields:

| Deferred chart | Required schema extension |
| --- | --- |
| Bubble | explicit x, y, size, and optional colour roles |
| Gauge/bullet | explicit value, target/benchmark, range, and target scope |
| Treemap | ordered category hierarchy plus size and optional colour roles |
| Radar/spider | three or more normalized metric axes and optional series |
| Sankey | explicit source category, target category, and flow-width measure |
| Geographic map | verified coordinates or a controlled geographic resolver |
| Table/grid | a dashboard-native tabular artifact instead of a PNG-only chart |

Adding these requires a new visualization-spec schema version, migration tests,
builder controls, report/PDF presentation rules, and equivalent Ollama schema
constraints.
