import json
from pathlib import Path

import pytest

from insight_reporter.dataset_chat import answer_dataset_question
from insight_reporter.dataset_profile import profile_dataset
from insight_reporter.dataset_view import load_dataset_view
from insight_reporter.ollama_query_planner import QueryPlannerResult, plan_query_with_ollama
from insight_reporter.query_data_store import QueryDataStore
from insight_reporter.query_plan_compiler import (
    QueryPlanClarification,
    QueryPlanError,
    compile_query_plan,
    execute_compiled_plan,
)
from insight_reporter.query_understanding import generate_suggested_questions


def _profiled_view(path: Path):  # type: ignore[no-untyped-def]
    view = load_dataset_view(path)
    profile = profile_dataset(view, size_bytes=path.stat().st_size)
    return view, profile


class _RepairPlannerClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, **_kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls == 1:
            return {"message": {"content": '{"intent":"query"}'}}
        if self.calls == 2:
            return {
                "message": {
                    "content": (
                        '{"status":"needs_clarification",'
                        '"message":"Could not choose a country column."}'
                    )
                }
            }
        return {
            "message": {
                "content": (
                    '{"status":"ready","intent":"filtered_grouped_aggregate",'
                    '"metric":"Units_Received","aggregation":"avg",'
                    '"filters":[{"column":"Category","operator":"equals",'
                    '"value":"Electronics"}],"group_by":["Country"],'
                    '"order_by":[],"limit":100,'
                    '"assumptions":["Interpreted countries as Country."]}'
                )
            }
        }


class _RecordingPlannerClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def chat(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return {"message": {"content": '{"intent":"query"}'}}
        return {
            "message": {
                "content": (
                    '{"status":"ready","analysis_type":"filtered_aggregate",'
                    '"measure":{"column":"Sales","aggregation":"sum"},'
                    '"dimensions":[],"filters":[],"time":null,"comparisons":{},'
                    '"buckets":[],"limit":100,"assumptions":[]}'
                )
            }
        }


def test_dataset_chat_answers_boolean_rate_by_named_dimension(tmp_path: Path) -> None:
    path = tmp_path / "hr.csv"
    path.write_text(
        "\n".join(
            (
                "Department,Attrition,MonthlyIncome",
                "Sales,Yes,100",
                "Sales,No,200",
                "Engineering,No,300",
                "Engineering,No,400",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)

    turn = answer_dataset_question(
        "Which departments have the highest attrition rate?",
        view=view,
        profile=profile,
    )

    assert turn.analysis_request.intent == "boolean_rate"
    assert turn.analysis_request.target_columns == ("Attrition",)
    assert turn.analysis_request.dimension_columns == ("Department",)
    assert "Sales has the highest Attrition rate" in turn.answer
    assert "50%" in turn.answer


def test_dataset_chat_matches_camel_case_metric_without_age_substring(tmp_path: Path) -> None:
    path = tmp_path / "income.csv"
    path.write_text(
        "\n".join(
            (
                "Department,Age,MonthlyIncome",
                "Sales,25,100",
                "Sales,35,200",
                "Engineering,40,500",
                "Engineering,45,700",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)

    turn = answer_dataset_question(
        "Which groups have the highest average monthly income?",
        view=view,
        profile=profile,
    )

    assert turn.analysis_request.intent == "top_bottom"
    assert turn.analysis_request.metric_columns[0] == "MonthlyIncome"
    assert "MonthlyIncome" in turn.answer
    assert "Age" not in turn.answer


def test_dataset_chat_reports_missingness_without_model(tmp_path: Path) -> None:
    path = tmp_path / "missing.csv"
    path.write_text(
        "\n".join(
            (
                "Region,Value",
                "North,10",
                "South,",
                "East,30",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)

    turn = answer_dataset_question(
        "Are there missing values?",
        view=view,
        profile=profile,
    )

    assert turn.model_status == "deterministic_only"
    assert "Value has the highest missingness" in turn.answer


def test_validated_query_plan_handles_filtered_quarter_aggregate(tmp_path: Path) -> None:
    path = tmp_path / "sales.csv"
    path.write_text(
        "\n".join(
            (
                "Country,Supplier,OrderDate,Sales",
                "Singapore,Alpha,2026-04-10,100",
                "Singapore,Alpha,2026-05-10,300",
                "Singapore,Beta,2026-04-10,999",
                "India,Alpha,2026-04-10,500",
                "Singapore,Alpha,2026-01-10,700",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    store = QueryDataStore.from_view(view, profile=profile)
    plan = {
        "status": "ready",
        "intent": "filtered_aggregate",
        "metric": "Sales",
        "aggregation": "avg",
        "filters": [
            {"column": "Country", "operator": "equals", "value": "Singapore"},
            {"column": "Supplier", "operator": "equals", "value": "Alpha"},
            {"column": "OrderDate", "operator": "quarter", "value": 2},
        ],
        "group_by": [],
        "order_by": [],
        "limit": 100,
    }

    compiled = compile_query_plan(plan, profile=profile, table_name=store.table_name)
    insight = execute_compiled_plan(
        compiled,
        store=store,
        question="What is the mean sales in Singapore for Q2 for Alpha supplier?",
    )

    assert insight.insight_type == "validated_model_query"
    assert insight.supporting_data == ({"value": 200.0, "records": 2},)
    assert "mean Sales is 200" in insight.finding


def test_analysis_plan_handles_time_series_without_month_filter(tmp_path: Path) -> None:
    path = tmp_path / "gross_sales.csv"
    path.write_text(
        "\n".join(
            (
                "Month_Name,Gross_Sales",
                "Jan-2025,100",
                "Jan-2025,50",
                "Feb-2025,200",
                "Mar-2025,125",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    store = QueryDataStore.from_view(view, profile=profile)
    plan = {
        "status": "ready",
        "analysis_type": "time_series",
        "measure": {"column": "Gross_Sales", "aggregation": "sum"},
        "dimensions": ["Month_Name"],
        "filters": [],
        "time": {"column": "Month_Name", "grain": "month", "operation": "trend"},
        "comparisons": {"sort": "chronological"},
        "limit": 100,
        "assumptions": ["Interpreted Month_Name as the month dimension."],
    }

    compiled = compile_query_plan(plan, profile=profile, table_name=store.table_name)
    insight = execute_compiled_plan(
        compiled,
        store=store,
        question="What trend is seen in gross sales across months?",
    )

    assert compiled.analysis_type == "time_series"
    assert compiled.filters == ()
    assert insight.supporting_data == (
        {"Month_Name": "Jan-2025", "value": 150.0, "records": 2},
        {"Month_Name": "Feb-2025", "value": 200.0, "records": 1},
        {"Month_Name": "Mar-2025", "value": 125.0, "records": 1},
    )
    assert "total Gross_Sales across Month_Name" in insight.finding
    assert "highest period is Feb-2025" in insight.finding


def test_analysis_plan_lists_distinct_values(tmp_path: Path) -> None:
    path = tmp_path / "countries.csv"
    path.write_text(
        "\n".join(
            (
                "Country,Sales",
                "Singapore,100",
                "India,200",
                "Singapore,300",
                "Malaysia,150",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    store = QueryDataStore.from_view(view, profile=profile)
    plan = {
        "status": "ready",
        "analysis_type": "distinct_values",
        "measure": None,
        "dimensions": ["Country"],
        "filters": [],
        "limit": 100,
        "assumptions": [],
    }

    compiled = compile_query_plan(plan, profile=profile, table_name=store.table_name)
    insight = execute_compiled_plan(
        compiled,
        store=store,
        question='list the different country names in "Country" column',
    )

    assert compiled.analysis_type == "distinct_values"
    assert insight.supporting_data == (
        {"value": "India", "records": 1},
        {"value": "Malaysia", "records": 1},
        {"value": "Singapore", "records": 2},
    )
    assert "Country has 3 distinct value" in insight.finding


def test_analysis_plan_categorizes_numeric_column_with_buckets(tmp_path: Path) -> None:
    path = tmp_path / "inventory.csv"
    path.write_text(
        "\n".join(
            (
                "Product,Inventory_Level",
                "A,10",
                "B,55",
                "C,120",
                "D,140",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    store = QueryDataStore.from_view(view, profile=profile)
    plan = {
        "status": "ready",
        "analysis_type": "categorization",
        "measure": {"column": "Inventory_Level", "aggregation": "count"},
        "bucket_column": "Inventory_Level",
        "filters": [],
        "buckets": [
            {"label": "Low", "operator": "lt", "value": 50},
            {"label": "Medium", "operator": "between", "value": 50, "upper": 100},
            {"label": "High", "operator": "gt", "value": 100},
        ],
        "limit": 100,
        "assumptions": [],
    }

    compiled = compile_query_plan(plan, profile=profile, table_name=store.table_name)
    insight = execute_compiled_plan(
        compiled,
        store=store,
        question="categorize inventory level into low medium high",
    )

    assert compiled.analysis_type == "categorization"
    assert {"category": "High", "records": 2, "average_value": 130.0} in insight.supporting_data
    assert "categorized by Inventory_Level" in insight.finding


def test_ranking_plan_returns_top_item_within_each_filtered_group(tmp_path: Path) -> None:
    path = tmp_path / "products.csv"
    path.write_text(
        "\n".join(
            (
                "Country,Product,Units_Sold",
                "USA,Laptop,10",
                "USA,Phone,30",
                "Singapore,Laptop,40",
                "Singapore,Phone,20",
                "Netherlands,Laptop,5",
                "Netherlands,Tablet,25",
                "India,Phone,999",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    store = QueryDataStore.from_view(view, profile=profile)
    plan = {
        "status": "ready",
        "analysis_type": "ranking",
        "measure": {"column": "Units_Sold", "aggregation": "sum"},
        "dimensions": ["Country", "Product"],
        "filters": [
            {
                "column": "Country",
                "operator": "in",
                "value": ["USA", "Singapore", "Netherlands"],
            }
        ],
        "limit": 100,
        "assumptions": [],
    }

    compiled = compile_query_plan(plan, profile=profile, table_name=store.table_name)
    insight = execute_compiled_plan(
        compiled,
        store=store,
        question="Which are highest products sold in each of the countries USA, Singapore and Netherlands respectively?",
    )

    assert insight.supporting_data == (
        {"parent_value": "Netherlands", "item_value": "Tablet", "value": 25.0, "records": 1},
        {"parent_value": "Singapore", "item_value": "Laptop", "value": 40.0, "records": 1},
        {"parent_value": "USA", "item_value": "Phone", "value": 30.0, "records": 1},
    )
    assert "Netherlands: Tablet (25)" in insight.finding
    assert "Singapore: Laptop (40)" in insight.finding
    assert "USA: Phone (30)" in insight.finding


def test_filtered_aggregate_drops_redundant_group_by_filter_column(tmp_path: Path) -> None:
    path = tmp_path / "inventory.csv"
    path.write_text(
        "\n".join(
            (
                "Product_Category,Beginning_Inventory_Units",
                "Electronics,10",
                "Electronics,30",
                "Furniture,100",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    store = QueryDataStore.from_view(view, profile=profile)
    plan = {
        "status": "ready",
        "analysis_type": "grouped_comparison",
        "measure": {"column": "Beginning_Inventory_Units", "aggregation": "avg"},
        "dimensions": ["Product_Category"],
        "filters": [
            {"column": "Product_Category", "operator": "equals", "value": "Electronics"}
        ],
        "limit": 100,
        "assumptions": [],
    }

    compiled = compile_query_plan(plan, profile=profile, table_name=store.table_name)
    insight = execute_compiled_plan(
        compiled,
        store=store,
        question="Average beginning inventory units for electronics product?",
    )

    assert compiled.analysis_type == "filtered_aggregate"
    assert compiled.group_by == ()
    assert insight.supporting_data == ({"value": 20.0, "records": 2},)
    assert "mean Beginning_Inventory_Units is 20" in insight.finding


def test_dataset_chat_reports_planner_fallback_reason(tmp_path: Path) -> None:
    path = tmp_path / "sales.csv"
    path.write_text(
        "\n".join(
            (
                "Country,Supplier,OrderDate,Sales",
                "Singapore,Alpha,2026-04-10,100",
                "Singapore,Alpha,2026-05-10,300",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)

    turn = answer_dataset_question(
        "Mean sales in Singapore for Q2 for Alpha supplier?",
        view=view,
        profile=profile,
        use_model_planner=True,
        model="missing-local-test-model",
        host="http://127.0.0.1:1",
        timeout_seconds=1,
    )

    assert turn.model_status == "fallback_after_planner_error"
    assert turn.planner_error


def test_ollama_planner_retries_needs_clarification_with_best_effort(
    tmp_path: Path,
) -> None:
    path = tmp_path / "inventory.csv"
    path.write_text(
        "\n".join(
            (
                "Country,Category,Units_Received",
                "Singapore,Electronics,100",
                "India,Electronics,200",
            )
        ),
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    client = _RepairPlannerClient()

    result = plan_query_with_ollama(
        "Mean units received for electronics across all countries",
        view=view,
        profile=profile,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
        client=client,
    )

    assert client.calls == 3
    assert result.plan["status"] == "ready"
    assert result.plan["group_by"] == ["Country"]
    assert result.plan["assumptions"] == ["Interpreted countries as Country."]


def test_query_plan_rejects_unknown_dimension_instead_of_broadening_query(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sales.csv"
    path.write_text("Region,Sales\nNorth,10\nSouth,20\n", encoding="utf-8")
    view, profile = _profiled_view(path)
    store = QueryDataStore.from_view(view, profile=profile)

    with pytest.raises(QueryPlanError, match="Unknown column: MissingRegion"):
        compile_query_plan(
            {
                "status": "ready",
                "analysis_type": "grouped_comparison",
                "measure": {"column": None, "aggregation": "count"},
                "dimensions": ["MissingRegion"],
                "filters": [],
            },
            profile=profile,
            table_name=store.table_name,
        )


def test_dataset_chat_routes_unknown_model_column_to_clarification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sales.csv"
    path.write_text("Region,Sales\nNorth,10\nSouth,20\n", encoding="utf-8")
    view, profile = _profiled_view(path)

    monkeypatch.setattr(
        "insight_reporter.dataset_chat.plan_query_with_ollama",
        lambda *_args, **_kwargs: QueryPlannerResult(
            plan={
                "status": "ready",
                "analysis_type": "grouped_comparison",
                "measure": {"column": "Sales", "aggregation": "avg"},
                "dimensions": ["MissingRegion"],
                "filters": [],
            },
            model="test-model",
            prompt_version="test",
        ),
    )

    turn = answer_dataset_question(
        "Average sales by missing region?",
        view=view,
        profile=profile,
        use_model_planner=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
    )

    assert turn.model_status == "needs_clarification"
    assert turn.insights == ()
    assert "MissingRegion" in turn.answer


def test_ollama_planner_separates_classification_and_schema_constrained_slot_filling(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sales.csv"
    path.write_text("Region,Sales\nNorth,10\nSouth,20\n", encoding="utf-8")
    view, profile = _profiled_view(path)
    client = _RecordingPlannerClient()

    result = plan_query_with_ollama(
        "What is total sales?",
        view=view,
        profile=profile,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
        client=client,
    )

    assert result.plan["status"] == "ready"
    assert len(client.calls) == 2
    classifier_format = client.calls[0]["format"]
    planner_format = client.calls[1]["format"]
    assert isinstance(classifier_format, dict)
    assert classifier_format["properties"]["intent"]["enum"] == [
        "query",
        "analysis",
        "overview",
        "clarify",
        "chitchat",
    ]
    assert isinstance(planner_format, dict)
    assert planner_format["additionalProperties"] is False
    assert not ({"joins", "having", "resolve"} & set(planner_format["properties"]))
    first_system_prompt = client.calls[0]["messages"][0]["content"]
    second_system_prompt = client.calls[1]["messages"][0]["content"]
    assert "Classify" in first_system_prompt
    assert "data analysis planner" in second_system_prompt


def test_planner_context_includes_live_low_cardinality_values_and_glossary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "orders.csv"
    path.write_text(
        "Region,Amount\nWest,10\nEast,20\nNorth,30\nSouth,40\n",
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    client = _RecordingPlannerClient()

    plan_query_with_ollama(
        "What is revenue by region?",
        view=view,
        profile=profile,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
        client=client,
    )

    payload = json.loads(client.calls[1]["messages"][1]["content"])
    region = next(item for item in payload["columns"] if item["name"] == "Region")
    assert region["distinct_values"] == ["East", "North", "South", "West"]
    assert payload["business_glossary"]["revenue"] == ["Amount"]


@pytest.mark.parametrize(
    ("intent", "question", "expected_status"),
    (
        ("chitchat", "Hello there", "chitchat"),
        ("overview", "What am I looking at?", "overview"),
        ("clarify", "Show me that thing", "needs_clarification"),
    ),
)
def test_dataset_chat_routes_non_query_intents_without_duckdb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    intent: str,
    question: str,
    expected_status: str,
) -> None:
    path = tmp_path / "sales.csv"
    path.write_text("Region,Sales\nNorth,10\nSouth,20\n", encoding="utf-8")
    view, profile = _profiled_view(path)
    monkeypatch.setattr(
        "insight_reporter.dataset_chat.plan_query_with_ollama",
        lambda *_args, **_kwargs: QueryPlannerResult(
            plan={"status": "routed", "intent": intent},
            model="test-model",
            prompt_version="test",
            intent=intent,
        ),
    )
    monkeypatch.setattr(
        "insight_reporter.dataset_chat.QueryDataStore.from_view",
        lambda *_args, **_kwargs: pytest.fail("DuckDB must not run for this intent"),
    )

    turn = answer_dataset_question(
        question,
        view=view,
        profile=profile,
        use_model_planner=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
    )

    assert turn.model_status == expected_status
    if intent == "overview":
        assert "2 rows" in turn.answer
        assert "2 columns" in turn.answer


def test_dataset_chat_routes_analysis_separately_from_query_compiler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cars.csv"
    path.write_text(
        "FuelType,FuelEfficiency\nPetrol,20\nPetrol,22\nDiesel,30\nDiesel,32\n",
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    monkeypatch.setattr(
        "insight_reporter.dataset_chat.plan_query_with_ollama",
        lambda *_args, **_kwargs: QueryPlannerResult(
            plan={
                "status": "ready",
                "intent": "analysis",
                "table": "uploaded_data",
                "target": "FuelEfficiency",
                "factor": "FuelType",
            },
            model="test-model",
            prompt_version="test",
            intent="analysis",
        ),
    )

    turn = answer_dataset_question(
        "Does fuel type affect fuel efficiency?",
        view=view,
        profile=profile,
        use_model_planner=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
    )

    assert turn.model_status == "analysis_routed"
    assert turn.analysis_request.intent == "relationship"
    assert turn.insights[0].calculation == "one_way_anova"
    assert turn.insights[0].supporting_data[0]["p_value"] < 0.05
    assert turn.insights[0].supporting_data[0]["significant_at_0.05"] is True
    assert "p=" in turn.answer
    assert "does not establish causation" in turn.answer


@pytest.mark.parametrize(
    ("csv_text", "target", "factor", "expected_calculation"),
    (
        (
            "X,Y\n1,2\n2,4\n3,6\n4,8\n",
            "Y",
            "X",
            "pearson_correlation",
        ),
        (
            "Segment,Outcome\nA,Yes\nA,Yes\nA,No\nB,No\nB,No\nB,Yes\n",
            "Outcome",
            "Segment",
            "chi_square_independence",
        ),
    ),
)
def test_statistical_analysis_dispatches_by_dtype(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    csv_text: str,
    target: str,
    factor: str,
    expected_calculation: str,
) -> None:
    path = tmp_path / "analysis.csv"
    path.write_text(csv_text, encoding="utf-8")
    view, profile = _profiled_view(path)
    monkeypatch.setattr(
        "insight_reporter.dataset_chat.plan_query_with_ollama",
        lambda *_args, **_kwargs: QueryPlannerResult(
            plan={
                "status": "ready",
                "intent": "analysis",
                "table": "uploaded_data",
                "target": target,
                "factor": factor,
            },
            model="test-model",
            prompt_version="test",
            intent="analysis",
        ),
    )

    turn = answer_dataset_question(
        f"Does {factor} affect {target}?",
        view=view,
        profile=profile,
        use_model_planner=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
    )

    assert turn.insights[0].calculation == expected_calculation
    assert "p_value" in turn.insights[0].supporting_data[0]
    assert "significant_at_0.05" in turn.insights[0].supporting_data[0]


def test_statistical_analysis_reports_non_significance_plainly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "unrelated.csv"
    path.write_text(
        "Group,Value\nA,10\nA,20\nA,30\nB,12\nB,18\nB,30\n",
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    monkeypatch.setattr(
        "insight_reporter.dataset_chat.plan_query_with_ollama",
        lambda *_args, **_kwargs: QueryPlannerResult(
            plan={
                "status": "ready",
                "intent": "analysis",
                "table": "uploaded_data",
                "target": "Value",
                "factor": "Group",
            },
            model="test-model",
            prompt_version="test",
            intent="analysis",
        ),
    )

    turn = answer_dataset_question(
        "Does group affect value?",
        view=view,
        profile=profile,
        use_model_planner=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
    )

    assert turn.insights[0].supporting_data[0]["significant_at_0.05"] is False
    assert "No meaningful relationship was found" in turn.answer


def test_overview_followup_uses_last_result_from_conversation_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "sales.csv"
    path.write_text("Region,Sales\nNorth,10\nSouth,20\n", encoding="utf-8")
    view, profile = _profiled_view(path)
    state: dict[str, object] = {}

    def fake_plan(question: str, **_kwargs):  # type: ignore[no-untyped-def]
        if "looking" in question:
            return QueryPlannerResult(
                plan={"status": "routed", "intent": "overview"},
                model="test-model",
                prompt_version="test",
                intent="overview",
            )
        return QueryPlannerResult(
            plan={
                "status": "ready",
                "analysis_type": "filtered_aggregate",
                "measure": {"column": "Sales", "aggregation": "sum"},
                "dimensions": [],
                "filters": [],
            },
            model="test-model",
            prompt_version="test",
            intent="query",
        )

    monkeypatch.setattr("insight_reporter.dataset_chat.plan_query_with_ollama", fake_plan)

    first = answer_dataset_question(
        "What is total sales?",
        view=view,
        profile=profile,
        use_model_planner=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
        conversation_state=state,
    )
    followup = answer_dataset_question(
        "What am I looking at?",
        view=view,
        profile=profile,
        use_model_planner=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
        conversation_state=state,
    )

    assert first.model_status == "ollama_plan_validated"
    assert state["last_question"] == "What is total sales?"
    assert state["last_sql"]
    assert state["last_result"] == [{"value": 30.0, "records": 2}]
    assert "previous question was “What is total sales?”" in followup.answer
    assert "30" in followup.answer


def test_concrete_trend_question_does_not_reuse_previous_overview_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "costs.csv"
    path.write_text(
        "RecordedAt,Region,Unit_Cost_USD\n"
        "2025-01-01,APAC,10\n"
        "2025-02-01,Europe,20\n"
        "2025-03-01,APAC,30\n",
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    state: dict[str, object] = {
        "last_question": "Does Region affect Unit_Cost_USD?",
        "last_sql": None,
        "last_result": [{"f_statistic": 0.626398, "p_value": 0.534559}],
    }

    monkeypatch.setattr(
        "insight_reporter.dataset_chat.plan_query_with_ollama",
        lambda *_args, **_kwargs: QueryPlannerResult(
            plan={"status": "routed", "intent": "overview"},
            model="test-model",
            prompt_version="test",
            intent="overview",
        ),
    )

    turn = answer_dataset_question(
        "How has Unit_Cost_USD changed over time?",
        view=view,
        profile=profile,
        use_model_planner=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
        conversation_state=state,
    )

    assert turn.model_status == "fallback_after_planner_error"
    assert turn.analysis_request.intent == "trend"
    assert turn.insights
    assert "previous question" not in turn.answer
    assert state["last_question"] == "How has Unit_Cost_USD changed over time?"


def test_schema_and_last_answer_drive_suggested_questions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "cars.csv"
    path.write_text(
        "FuelType,FuelEfficiency,RecordedAt\nPetrol,20,2026-01-01\nDiesel,30,2026-02-01\n",
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)

    startup = generate_suggested_questions(profile)

    assert "What is the average FuelEfficiency by FuelType?" in startup
    assert "Does FuelType affect FuelEfficiency?" in startup
    assert "What am I looking at?" in startup

    monkeypatch.setattr(
        "insight_reporter.dataset_chat.plan_query_with_ollama",
        lambda *_args, **_kwargs: QueryPlannerResult(
            plan={
                "status": "ready",
                "analysis_type": "grouped_comparison",
                "measure": {"column": "FuelEfficiency", "aggregation": "avg"},
                "dimensions": ["FuelType"],
                "filters": [],
            },
            model="test-model",
            prompt_version="test",
            intent="query",
        ),
    )
    turn = answer_dataset_question(
        "Average fuel efficiency by fuel type?",
        view=view,
        profile=profile,
        use_model_planner=True,
        model="test-model",
        host="http://127.0.0.1:11434",
        timeout_seconds=1,
    )

    assert turn.suggested_questions
    assert "FuelType" in turn.suggested_questions[0]


def test_string_filter_values_are_grounded_or_clarified(tmp_path: Path) -> None:
    path = tmp_path / "sales.csv"
    path.write_text(
        "Region,Sales\nWest,10\nEast,20\nWest,30\n",
        encoding="utf-8",
    )
    view, profile = _profiled_view(path)
    store = QueryDataStore.from_view(view, profile=profile)
    base_plan = {
        "status": "ready",
        "analysis_type": "filtered_aggregate",
        "measure": {"column": "Sales", "aggregation": "sum"},
        "dimensions": [],
        "filters": [{"column": "Region", "operator": "equals", "value": "Wst"}],
    }

    compiled = compile_query_plan(
        base_plan,
        profile=profile,
        table_name=store.table_name,
        store=store,
    )
    insight = execute_compiled_plan(compiled, store=store, question="Sales in Wst?")

    assert compiled.params == ("West",)
    assert insight.supporting_data == ({"value": 40.0, "records": 2},)

    bad_plan = {
        **base_plan,
        "filters": [{"column": "Region", "operator": "equals", "value": "Atlantis"}],
    }
    with pytest.raises(QueryPlanClarification, match="couldn't find a confident match"):
        compile_query_plan(
            bad_plan,
            profile=profile,
            table_name=store.table_name,
            store=store,
        )
