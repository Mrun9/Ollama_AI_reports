# Derived KPI Formula Guide

This guide explains how to build useful derived KPIs in AI Insight Reporter without needing
programming or advanced statistics knowledge.

The application supports two types of formula:

1. **Row formulas** create a virtual value for every dataset row and then summarize those values.
2. **Aggregate formulas** calculate one measure from a group of rows, such as the whole dataset,
   one month, or one category.

All formulas are parsed and calculated by Python. They are never executed with `eval`, sent to a
shell, or calculated by Ollama.

## Quick decision guide

Start by completing this sentence:

> I want to measure...

- **something for every individual record** → use a **row formula**
- **a rate, ratio, or overall measure for a group of records** → use an **aggregate formula**

Examples:

| Question | Recommended type | Example |
|---|---|---|
| What is the profit on each transaction? | Row | `[revenue] - [cost]` |
| What is the average risk score for each student? | Row | `([stress] + [anxiety]) / 2` |
| What is the overall profit margin? | Aggregate | `(SUM([revenue]) - SUM([cost])) / SUM([revenue]) * 100` |
| What percentage of records have a positive binary label? | Aggregate | `MEAN([label]) * 100` |
| What is revenue per unit sold? | Aggregate | `SUM([revenue]) / SUM([units])` |

## Formula syntax

### Referencing columns

Always put an exact numeric column name inside square brackets:

```text
[revenue]
[daily_social_media_hours]
[gross revenue]
```

Column names are case-sensitive in practice because they must match the uploaded dataset exactly.
Copy the token displayed by the formula builder when possible.

### Supported operators

```text
+
-
*
/
()
```

The scalar function `ABS(...)` is also supported:

```text
ABS([actual] - [target])
```

### Supported aggregate functions

Aggregate formulas may use:

```text
SUM([column])
MEAN([column])
MEDIAN([column])
MIN([column])
MAX([column])
COUNT([column])
```

`COUNT([column])` counts non-missing numeric values in that column.

Every column in an aggregate formula must be inside an aggregate function. This is valid:

```text
SUM([revenue]) / SUM([units])
```

This is not valid as an aggregate formula:

```text
SUM([revenue]) / [units]
```

## Row formulas

A row formula is calculated once for every source record. The resulting values behave like a
virtual numeric column; the uploaded file itself is not modified.

Suppose the source contains:

| revenue | cost |
|---:|---:|
| 100 | 60 |
| 200 | 150 |

The formula:

```text
[revenue] - [cost]
```

produces:

| revenue | cost | virtual profit |
|---:|---:|---:|
| 100 | 60 | 40 |
| 200 | 150 | 50 |

The **Aggregation** field then tells the report how to combine those virtual row values:

| Aggregation | Result in this example | Typical meaning |
|---|---:|---|
| Sum | 90 | Total profit |
| Mean | 45 | Average profit per record |
| Median | 45 | Typical middle profit |
| Min | 40 | Lowest profit |
| Max | 50 | Highest profit |

The same aggregation is recalculated within each selected period or category.

### Row formula examples

#### Profit

```text
[revenue] - [cost]
```

Suggested configuration:

- Calculation level: `row`
- Aggregation: `sum`
- Display format: `currency`
- Direction: `higher`

#### Composite mental-health risk score

Use this only when all three columns use a compatible scale:

```text
([stress_level] + [anxiety_level] + [addiction_level]) / 3
```

Suggested configuration:

- Calculation level: `row`
- Aggregation: `mean`
- Display format: `number`
- Direction: `lower`

This is a user-defined composite indicator, not a clinical diagnosis.

#### Absolute target deviation

```text
ABS([actual_value] - [target_value])
```

Suggested configuration:

- Calculation level: `row`
- Aggregation: `mean`
- Display format: `number`
- Direction: `lower`

#### Total resource usage per record

```text
[cpu_hours] + [gpu_hours] + [storage_hours]
```

Choose `sum` if the objective is total usage or `mean` if the objective is typical usage per
record.

## Aggregate formulas

An aggregate formula does not create a separate KPI value for each row. It calculates one KPI for
the current group of rows.

A group might be:

- the complete dataset
- one calendar period
- one platform, region, gender, department, or other selected category

For the earlier revenue example:

```text
(SUM([revenue]) - SUM([cost])) / SUM([revenue]) * 100
```

Python calculates:

```text
Total revenue = 300
Total cost = 210
Profit margin = (300 - 210) / 300 * 100 = 30%
```

For aggregate formulas, select:

- Calculation level: `aggregate`
- Aggregation: `formula`

The aggregate functions already describe how the source rows must be combined, so a second
aggregation choice is unnecessary.

### Aggregate formula examples

#### Depression-label rate

For a binary column where `0` means negative and `1` means positive:

```text
MEAN([depression_label]) * 100
```

Suggested configuration:

- KPI name: `Depression Rate`
- Calculation level: `aggregate`
- Aggregation: `formula`
- Display format: `percentage`
- Direction: `lower`
- Date: none, unless the source genuinely contains a date
- Categories: for example `platform_usage`, `gender`, `social_interaction_level`

Do not describe category differences as causes.

#### Conversion rate

When `conversions` and `visits` are numeric counts:

```text
SUM([conversions]) / SUM([visits]) * 100
```

Suggested configuration:

- Calculation level: `aggregate`
- Aggregation: `formula`
- Display format: `percentage`
- Direction: `higher`

#### Defect rate from a binary flag

For `defect_flag` values of `0` and `1`:

```text
MEAN([defect_flag]) * 100
```

Suggested direction: `lower`.

#### Average order value

When each non-missing revenue value represents one order:

```text
SUM([revenue]) / COUNT([revenue])
```

This is equivalent to `MEAN([revenue])`, but the ratio form makes the business definition
explicit.

#### Revenue per unit

```text
SUM([revenue]) / SUM([quantity])
```

Suggested display format: `currency`; suggested direction: normally `higher`.

#### Overall profit margin

```text
(SUM([revenue]) - SUM([cost])) / SUM([revenue]) * 100
```

Suggested display format: `percentage`; suggested direction: `higher`.

## Why row and aggregate results can differ

Consider:

| revenue | cost | row margin |
|---:|---:|---:|
| 100 | 60 | 40% |
| 200 | 150 | 25% |

The row formula:

```text
([revenue] - [cost]) / [revenue] * 100
```

with `mean` aggregation produces:

```text
(40% + 25%) / 2 = 32.5%
```

The aggregate formula:

```text
(SUM([revenue]) - SUM([cost])) / SUM([revenue]) * 100
```

produces:

```text
(300 - 210) / 300 * 100 = 30%
```

These answer different questions:

- `32.5%` is the average of individual row margins.
- `30%` is the overall margin weighted by revenue.

Choose the definition that matches the business question. Do not select a formula only because its
result looks more favorable.

## Conditional category rates and shares

Conditional KPIs use a dedicated builder instead of formula text. Use it when
the question contains “where,” “only,” or “for this status/type,” and the
numerator depends on exact category values.

### New versus Returning revenue

To answer “What percentage of revenue came from New customers?” configure:

- KPI name: `New Customer Revenue Share`
- Calculation base: `Value share`
- Numeric value column: `Net_Sales`
- Condition column: `Customer_Type`
- Numerator value: `New`
- Display format: fixed to `percentage`
- Direction: choose the meaning appropriate to the business; a larger new
  share is not automatically better
- Optional target: a user-approved percentage from 0 to 100

Python calculates:

```text
SUM(Net_Sales where Customer_Type is New)
------------------------------------------------ × 100
SUM(Net_Sales for all rows with valid Net_Sales)
```

This yields a statement such as “New customers generated 35% of Net Sales.”
To show both New and Returning composition, also configure `Customer_Type` as
a category for a normal additive `Net_Sales` source KPI. The automatic
category-share insight calculates and reconciles every named group to 100%.

### Return/Cancellation Rate

When one row represents one order or other intended event, configure:

- KPI name: `Return/Cancellation Rate`
- Calculation base: `Record count`
- Condition column: `Status`
- Numerator values: `Returned` and `Cancelled`
- Confirm: one row represents one denominator event
- Direction: `lower`
- Optional target: for example an approved target of `5`, meaning 5%

Python calculates:

```text
COUNT(rows where Status is Returned or Cancelled)
------------------------------------------------- × 100
COUNT(all rows)
```

Do not use this record-count definition when one order appears on several
product lines. The current builder does not perform distinct order counting.
Prepare one row per event first or use already aggregated count columns in an
approved aggregate formula.

### Conditional calculation policies

- Exact condition values come from the retained categorical/boolean column;
  they are not typed freely or invented by AI.
- Missing condition values stay in a record-count denominator.
- Value-share rows with missing/non-numeric measure values are excluded from
  numerator and denominator.
- A zero denominator returns `null`.
- Percentages are recalculated within each dataset, period, or segment; group
  percentages are not added or averaged.
- The conditional builder supports equality membership only. Numeric
  comparisons, date conditions, nested Boolean logic, and distinct counts are
  not yet supported.

## Configuration fields

After writing a formula, configure the following:

### KPI name

Use a short name that describes the result and, when useful, its unit:

- `Profit`
- `Depression Rate`
- `Average Risk Score`
- `Revenue per Unit`

### Calculation level

- `row`: calculate each record first
- `aggregate`: calculate one measure over each analysis group

### Aggregation

For row formulas:

- `sum` for additive totals
- `mean` for average per record
- `median` for a value less affected by extremes
- `min` or `max` for boundary monitoring

For aggregate formulas, select `formula`.

### Display format

- `number` for scores, counts, hours, units, and general values
- `percentage` for formulas that already return a percentage
- `currency` for monetary values

Percentage formulas should normally include `* 100`.

### Direction

Direction describes what the organization considers favorable:

- Higher is better: revenue, profit, conversion rate, academic performance
- Lower is better: defect rate, depression-label rate, processing time, cost

Direction is business context; Python cannot infer it safely from column values alone.

### Date column

Select a date only when the source has a genuine, consistently populated date column. Without one,
the system still produces non-temporal insights and explicitly skips period comparisons and trends.
With at least three eligible periods, the latest grouped KPI is also compared
with the mean of up to four preceding period aggregates.

### Category columns

Select categories that create meaningful comparison groups:

- region
- platform
- department
- customer type
- gender
- social-interaction level

Avoid selecting identifiers such as customer ID because they create one group per record.
When a date is also configured, each category value acts as a possible 6C
cohort. Cohort movement is calculated only for values with at least two valid
KPI records in both latest comparison periods.

### Target or benchmark

Use a target only when it has a defensible source, such as:

- an approved organizational goal
- a regulatory threshold
- a contract requirement
- a documented historical baseline

Leaving the benchmark blank is better than inventing one. The optional dataset-mean helper is
descriptive and should not automatically be treated as a business target.

Always select what the target applies to:

| Scope | Meaning | Required context |
|---|---|---|
| Each row | Compare every valid row-level KPI value with the target | Source KPI or row formula |
| Each period | Recalculate the KPI for every eligible period and compare each aggregate | Date column |
| Each segment | Recalculate the KPI for every eligible category value and compare each aggregate | At least one category column |
| Complete dataset | Compare the KPI calculated once over all eligible records | No additional grouping |

Aggregate formulas and conditional percentages do not have one KPI value per
row, so they reject row scope. They can use period, segment, or complete
dataset scope because the formula is recalculated inside the selected group.

### Business objective

State what should be measured, which comparisons matter, and any interpretation boundary:

> Measure and compare depression-label prevalence across platform and demographic segments to
> identify groups with higher observed rates, without implying causation.

## Which insight types work best with each formula?

| Insight type | Row formula | Aggregate formula |
|---|---:|---:|
| Period-over-period change | Yes | Yes |
| Trend by period | Yes | Yes |
| Category ranking | Yes | Yes |
| Segment contribution to change | Only when aggregation is `sum` | Skipped because the measure is non-additive |
| IQR row-level anomalies | Yes | Not applicable |
| Row-level numeric associations | Yes | Not applicable |
| Row-level benchmark breaches | Yes | Not applicable |
| Period aggregate versus target | Yes | Yes |
| Segment aggregate versus target | Yes | Yes |
| Complete-dataset aggregate versus target | Yes | Yes |
| Missing-data warnings | Yes | Yes |

Aggregate KPIs remain useful for rates and ratios, but they do not have one KPI value per source
row. Analyses that require row-level KPI values are therefore skipped safely.

## Safe and efficient workflow

1. **Start with the business question**, not the available operators.
2. **Confirm the source columns are numeric** and use compatible units.
3. **Choose row or aggregate level** using the quick decision guide.
4. **Enter the smallest formula that answers the question.**
5. Select **Recalculate Python preview**.
6. Review:
   - valid result count
   - missing-input count
   - division-by-zero count
   - non-finite result count
   - minimum, maximum, and mean
7. Correct unexpected nulls before confirming the KPI.
8. Select only meaningful dates and categories.
9. Add a benchmark only when its origin can be explained.
10. Generate deterministic insights and verify the Milestone 4A evidence card.

For a row formula, a low valid-result count usually indicates missing or non-numeric inputs. For an
aggregate formula, one valid preview result is normal because it represents the complete preview
group.

## Common mistakes

### Using a binary label directly as an additive KPI

Selecting `depression_label` directly makes sum-based analysis count positive records. Counts can
be misleading when segment sizes differ.

Prefer:

```text
MEAN([depression_label]) * 100
```

when the business question concerns prevalence.

For text or Boolean category values, use the conditional KPI builder instead
of attempting to place a string condition inside formula text.

### Averaging percentages when a weighted ratio is required

The mean of row percentages is not always the same as the overall percentage. Use a ratio of sums
when different rows have different sizes or weights.

### Mixing incompatible units

This is mathematically valid but usually meaningless:

```text
[revenue] + [sleep_hours]
```

Only combine columns when the business meaning and units are compatible.

### Dividing without considering zero values

If a denominator is zero, the result becomes null and the preview records a division-by-zero
result. Python never replaces the denominator or invents a value.

### Treating an association as causation

A relationship between depression rate and platform usage does not prove the platform caused the
outcome. The final objective and narrative should use language such as “observed difference” or
“association.”

## Current formula limits

The formula language deliberately does not support:

- arbitrary Python
- `eval` or `exec`
- SQL
- arbitrary conditional `IF` statements (exact categorical membership is
  available through the conditional KPI builder)
- text concatenation
- date arithmetic
- window functions
- cross-file joins
- model-generated calculation code

A formula may reference up to 20 numeric columns and is subject to bounded length, expression-size,
and nesting limits.

If a required KPI needs a condition beyond the exact-category builder,
category bucketing, date arithmetic, or multiple-source relationships, that
transformation should be added as a separately validated feature rather than
encoded as unrestricted formula code.

## Final checklist

Before confirming a derived KPI, verify:

- [ ] The formula answers a specific business question.
- [ ] Every referenced column is numeric and uses the expected unit.
- [ ] Row versus aggregate level is intentional.
- [ ] The aggregation matches the meaning of the KPI.
- [ ] The percentage formula includes `* 100` when appropriate.
- [ ] The direction is correct.
- [ ] The preview has an acceptable number of valid results.
- [ ] Missing and zero-division counts are understood.
- [ ] Categories are meaningful and are not record identifiers.
- [ ] Any benchmark has a documented source.
- [ ] The business objective avoids unsupported causal claims.
