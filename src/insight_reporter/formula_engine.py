"""Restricted multi-variable KPI formula parser and deterministic evaluator."""

import math
import re
import statistics
from dataclasses import dataclass
from typing import Any

from insight_reporter.dataset_profile import ColumnType, DatasetProfile
from insight_reporter.dataset_view import ColumnReference

MAX_FORMULA_CHARACTERS = 1_000
MAX_EXPRESSION_NODES = 100
MAX_EXPRESSION_DEPTH = 10
MAX_REFERENCED_COLUMNS = 20
ROW_AGGREGATIONS = frozenset({"sum", "mean", "median", "min", "max"})
AGGREGATE_FUNCTIONS = frozenset({"SUM", "MEAN", "MEDIAN", "MIN", "MAX", "COUNT"})
SCALAR_FUNCTIONS = frozenset({"ABS"})
_MISSING_MARKERS = frozenset({"", "na", "n/a", "null", "none", "nan"})


class FormulaError(ValueError):
    """Raised when a formula is invalid, unsafe, or mathematically undefined."""


@dataclass(frozen=True)
class FormulaEvaluation:
    value: float | None
    status: str


@dataclass(frozen=True)
class ParsedFormula:
    expression: dict[str, Any]
    formula_label: str
    calculation_level: str
    source_references: tuple[ColumnReference, ...]

    @property
    def source_columns(self) -> tuple[str, ...]:
        return tuple(reference.column for reference in self.source_references)

    def to_dict(self) -> dict[str, object]:
        return {
            "expression": self.expression,
            "formula_label": self.formula_label,
            "calculation_level": self.calculation_level,
            "source_references": [
                reference.to_dict() for reference in self.source_references
            ],
        }


@dataclass(frozen=True)
class _Token:
    kind: str
    value: str
    position: int


def parse_formula(
    formula: str,
    *,
    profile: DatasetProfile,
    source_id: str,
    calculation_level: str,
) -> ParsedFormula:
    """Parse a small formula language into a validated expression tree."""

    text = formula.strip()
    if not text or len(text) > MAX_FORMULA_CHARACTERS:
        raise FormulaError(
            f"Formula must contain 1 to {MAX_FORMULA_CHARACTERS} characters."
        )
    if calculation_level not in {"row", "aggregate"}:
        raise FormulaError("Formula calculation level must be row or aggregate.")
    tokens = _tokenize(text)
    if len(tokens) > (MAX_EXPRESSION_NODES * 2) + 1:
        raise FormulaError("Formula contains too many expression components.")
    parser = _Parser(tokens, profile=profile, source_id=source_id)
    expression = parser.parse()
    stats = _expression_stats(expression)
    if stats["nodes"] > MAX_EXPRESSION_NODES:
        raise FormulaError("Formula contains too many expression components.")
    if stats["depth"] > MAX_EXPRESSION_DEPTH:
        raise FormulaError("Formula nesting is too deep.")
    references = _references(expression)
    if not references or len(references) > MAX_REFERENCED_COLUMNS:
        raise FormulaError(
            f"Formula must reference between 1 and {MAX_REFERENCED_COLUMNS} columns."
        )
    raw_columns, aggregate_columns = _scope_counts(expression)
    if calculation_level == "row" and aggregate_columns:
        raise FormulaError("Row formulas cannot contain aggregate functions.")
    if calculation_level == "aggregate" and (raw_columns or not aggregate_columns):
        raise FormulaError(
            "Aggregate formulas must place every column inside an aggregate function."
        )
    return ParsedFormula(
        expression=expression,
        formula_label=_format_expression(expression),
        calculation_level=calculation_level,
        source_references=references,
    )


def load_parsed_formula(
    payload: object, *, profile: DatasetProfile, source_id: str
) -> ParsedFormula:
    """Reparse the canonical formula and verify its persisted tree."""

    expected = {
        "expression",
        "formula_label",
        "calculation_level",
        "source_references",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise FormulaError("Saved formula has an invalid shape.")
    label = payload.get("formula_label")
    level = payload.get("calculation_level")
    if not isinstance(label, str) or not isinstance(level, str):
        raise FormulaError("Saved formula contains invalid text.")
    parsed = parse_formula(
        label,
        profile=profile,
        source_id=source_id,
        calculation_level=level,
    )
    if payload.get("expression") != parsed.expression:
        raise FormulaError("Saved formula tree does not match its canonical formula.")
    if payload.get("source_references") != [
        reference.to_dict() for reference in parsed.source_references
    ]:
        raise FormulaError("Saved formula references do not match its expression.")
    return parsed


def evaluate_row_formula(
    parsed: ParsedFormula, values: dict[str, object]
) -> FormulaEvaluation:
    """Evaluate a row-level formula without executing source text."""

    if parsed.calculation_level != "row":
        raise FormulaError("Aggregate formulas cannot be evaluated as row formulas.")
    return _evaluate(parsed.expression, values=values, rows=None)


def evaluate_aggregate_formula(
    parsed: ParsedFormula, rows: tuple[dict[str, object], ...]
) -> FormulaEvaluation:
    """Evaluate an aggregate expression over a deterministic group of rows."""

    if parsed.calculation_level != "aggregate":
        raise FormulaError("Row formulas cannot be evaluated as aggregate formulas.")
    return _evaluate(parsed.expression, values=None, rows=rows)


def aggregate_row_values(values: list[float], aggregation: str) -> float | None:
    if not values:
        return None
    if aggregation == "sum":
        result = math.fsum(values)
    elif aggregation == "mean":
        result = math.fsum(values) / len(values)
    elif aggregation == "median":
        result = statistics.median(values)
    elif aggregation == "min":
        result = min(values)
    elif aggregation == "max":
        result = max(values)
    else:
        raise FormulaError("Row-formula aggregation is not supported.")
    return _clean(result) if math.isfinite(result) else None


class _Parser:
    def __init__(
        self,
        tokens: tuple[_Token, ...],
        *,
        profile: DatasetProfile,
        source_id: str,
    ) -> None:
        self.tokens = tokens
        self.profile = profile
        self.source_id = source_id
        self.index = 0
        self.nesting = 0

    def parse(self) -> dict[str, Any]:
        expression = self._expression()
        if self._peek().kind != "EOF":
            raise FormulaError(
                f"Unexpected formula token near position {self._peek().position + 1}."
            )
        return expression

    def _expression(self) -> dict[str, Any]:
        node = self._term()
        while self._peek().kind in {"+", "-"}:
            operator = self._advance().kind
            node = {
                "type": "binary",
                "operator": operator,
                "left": node,
                "right": self._term(),
            }
        return node

    def _term(self) -> dict[str, Any]:
        node = self._factor()
        while self._peek().kind in {"*", "/"}:
            operator = self._advance().kind
            node = {
                "type": "binary",
                "operator": operator,
                "left": node,
                "right": self._factor(),
            }
        return node

    def _factor(self) -> dict[str, Any]:
        if self._peek().kind in {"+", "-"}:
            operator = self._advance().kind
            self._enter_nesting()
            try:
                operand = self._factor()
            finally:
                self.nesting -= 1
            return {"type": "unary", "operator": operator, "operand": operand}
        return self._primary()

    def _primary(self) -> dict[str, Any]:
        token = self._advance()
        if token.kind == "NUMBER":
            value = float(token.value)
            if not math.isfinite(value) or abs(value) > 1e100:
                raise FormulaError("Formula numeric constant is invalid.")
            return {"type": "constant", "value": _clean(value)}
        if token.kind == "COLUMN":
            column = self.profile.column(token.value)
            if column is None or column.inferred_type is not ColumnType.NUMERIC:
                raise FormulaError(
                    f"Formula column [{token.value}] is not an existing numeric column."
                )
            return {
                "type": "column",
                "source_id": self.source_id,
                "column": token.value,
            }
        if token.kind == "(":
            self._enter_nesting()
            try:
                expression = self._expression()
                self._expect(")")
            finally:
                self.nesting -= 1
            return expression
        if token.kind == "IDENT":
            function = token.value.upper()
            if function not in AGGREGATE_FUNCTIONS | SCALAR_FUNCTIONS:
                raise FormulaError(f"Formula function {token.value} is not supported.")
            self._expect("(")
            self._enter_nesting()
            try:
                argument = self._expression()
                self._expect(")")
            finally:
                self.nesting -= 1
            if function in AGGREGATE_FUNCTIONS and argument.get("type") != "column":
                raise FormulaError(
                    f"{function} requires exactly one numeric column argument."
                )
            return {"type": "function", "name": function, "argument": argument}
        raise FormulaError(f"Expected a value near position {token.position + 1}.")

    def _expect(self, kind: str) -> _Token:
        token = self._advance()
        if token.kind != kind:
            raise FormulaError(
                f"Expected '{kind}' near position {token.position + 1}."
            )
        return token

    def _peek(self) -> _Token:
        return self.tokens[self.index]

    def _advance(self) -> _Token:
        token = self.tokens[self.index]
        if token.kind != "EOF":
            self.index += 1
        return token

    def _enter_nesting(self) -> None:
        self.nesting += 1
        if self.nesting > MAX_EXPRESSION_DEPTH:
            raise FormulaError("Formula nesting is too deep.")


def _tokenize(text: str) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    index = 0
    number_pattern = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
    identifier_pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    while index < len(text):
        character = text[index]
        if character.isspace():
            index += 1
            continue
        if character in "+-*/()":
            tokens.append(_Token(character, character, index))
            index += 1
            continue
        if character == "[":
            start = index
            index += 1
            value: list[str] = []
            while index < len(text):
                if text[index] == "]":
                    if index + 1 < len(text) and text[index + 1] == "]":
                        value.append("]")
                        index += 2
                        continue
                    index += 1
                    break
                value.append(text[index])
                index += 1
            else:
                raise FormulaError(
                    f"Formula column reference near position {start + 1} is not closed."
                )
            column = "".join(value)
            if not column:
                raise FormulaError("Formula column reference cannot be empty.")
            tokens.append(_Token("COLUMN", column, start))
            continue
        number_match = number_pattern.match(text, index)
        if number_match is not None:
            tokens.append(_Token("NUMBER", number_match.group(), index))
            index = number_match.end()
            continue
        identifier_match = identifier_pattern.match(text, index)
        if identifier_match is not None:
            tokens.append(_Token("IDENT", identifier_match.group(), index))
            index = identifier_match.end()
            continue
        raise FormulaError(f"Unsupported formula character near position {index + 1}.")
    tokens.append(_Token("EOF", "", len(text)))
    return tuple(tokens)


def _evaluate(
    node: dict[str, Any],
    *,
    values: dict[str, object] | None,
    rows: tuple[dict[str, object], ...] | None,
) -> FormulaEvaluation:
    node_type = node["type"]
    if node_type == "constant":
        return FormulaEvaluation(float(node["value"]), "valid")
    if node_type == "column":
        if values is None:
            raise FormulaError("Raw columns cannot appear outside aggregate functions.")
        number = _finite_number(values.get(node["column"]))
        return (
            FormulaEvaluation(number, "valid")
            if number is not None
            else FormulaEvaluation(None, "missing_input")
        )
    if node_type == "unary":
        operand = _evaluate(node["operand"], values=values, rows=rows)
        if operand.value is None:
            return operand
        result = operand.value if node["operator"] == "+" else -operand.value
        return _finite_result(result)
    if node_type == "binary":
        left = _evaluate(node["left"], values=values, rows=rows)
        if left.value is None:
            return left
        right = _evaluate(node["right"], values=values, rows=rows)
        if right.value is None:
            return right
        operator = node["operator"]
        if operator == "+":
            result = left.value + right.value
        elif operator == "-":
            result = left.value - right.value
        elif operator == "*":
            result = left.value * right.value
        elif operator == "/":
            if right.value == 0:
                return FormulaEvaluation(None, "division_by_zero")
            result = left.value / right.value
        else:
            raise FormulaError("Formula operator is not supported.")
        return _finite_result(result)
    if node_type == "function":
        function = node["name"]
        if function == "ABS":
            argument = _evaluate(node["argument"], values=values, rows=rows)
            if argument.value is None:
                return argument
            return _finite_result(abs(argument.value))
        if rows is None:
            raise FormulaError("Aggregate functions require a row group.")
        column = node["argument"]["column"]
        numbers = [
            number
            for row in rows
            if (number := _finite_number(row.get(column))) is not None
        ]
        if function == "COUNT":
            return FormulaEvaluation(float(len(numbers)), "valid")
        if not numbers:
            return FormulaEvaluation(None, "missing_input")
        if function == "SUM":
            result = math.fsum(numbers)
        elif function == "MEAN":
            result = math.fsum(numbers) / len(numbers)
        elif function == "MEDIAN":
            result = statistics.median(numbers)
        elif function == "MIN":
            result = min(numbers)
        elif function == "MAX":
            result = max(numbers)
        else:
            raise FormulaError("Formula function is not supported.")
        return _finite_result(result)
    raise FormulaError("Formula expression contains an unsupported node.")


def _finite_result(value: float) -> FormulaEvaluation:
    if not math.isfinite(value):
        return FormulaEvaluation(None, "non_finite_result")
    return FormulaEvaluation(_clean(value), "valid")


def _finite_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        candidate: object = value.strip()
        if str(candidate).casefold() in _MISSING_MARKERS:
            return None
    else:
        candidate = value
    try:
        number = float(candidate)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _references(node: dict[str, Any]) -> tuple[ColumnReference, ...]:
    found: list[ColumnReference] = []

    def visit(item: dict[str, Any]) -> None:
        if item["type"] == "column":
            reference = ColumnReference(item["source_id"], item["column"])
            if reference not in found:
                found.append(reference)
        elif item["type"] == "binary":
            visit(item["left"])
            visit(item["right"])
        elif item["type"] == "unary":
            visit(item["operand"])
        elif item["type"] == "function":
            visit(item["argument"])

    visit(node)
    return tuple(found)


def _scope_counts(node: dict[str, Any], *, inside_aggregate: bool = False) -> tuple[int, int]:
    node_type = node["type"]
    if node_type == "column":
        return (0, 1) if inside_aggregate else (1, 0)
    if node_type == "binary":
        left = _scope_counts(node["left"], inside_aggregate=inside_aggregate)
        right = _scope_counts(node["right"], inside_aggregate=inside_aggregate)
        return left[0] + right[0], left[1] + right[1]
    if node_type == "unary":
        return _scope_counts(node["operand"], inside_aggregate=inside_aggregate)
    if node_type == "function":
        return _scope_counts(
            node["argument"],
            inside_aggregate=inside_aggregate or node["name"] in AGGREGATE_FUNCTIONS,
        )
    return 0, 0


def _expression_stats(node: dict[str, Any]) -> dict[str, int]:
    node_type = node["type"]
    children: list[dict[str, Any]] = []
    if node_type == "binary":
        children = [node["left"], node["right"]]
    elif node_type == "unary":
        children = [node["operand"]]
    elif node_type == "function":
        children = [node["argument"]]
    if not children:
        return {"nodes": 1, "depth": 1}
    stats = [_expression_stats(child) for child in children]
    return {
        "nodes": 1 + sum(item["nodes"] for item in stats),
        "depth": 1 + max(item["depth"] for item in stats),
    }


def _format_expression(node: dict[str, Any], parent_precedence: int = 0) -> str:
    node_type = node["type"]
    if node_type == "constant":
        value = node["value"]
        return str(int(value)) if float(value).is_integer() else str(value)
    if node_type == "column":
        return f"[{str(node['column']).replace(']', ']]')}]"
    if node_type == "function":
        return f"{node['name']}({_format_expression(node['argument'])})"
    if node_type == "unary":
        return f"{node['operator']}{_format_expression(node['operand'], 3)}"
    if node_type == "binary":
        operator = node["operator"]
        precedence = 1 if operator in {"+", "-"} else 2
        left = _format_expression(node["left"], precedence)
        right = _format_expression(
            node["right"],
            precedence + (1 if operator in {"-", "/"} else 0),
        )
        text = f"{left} {operator} {right}"
        return f"({text})" if precedence < parent_precedence else text
    raise FormulaError("Formula expression contains an unsupported node.")


def _clean(value: float) -> float:
    rounded = round(float(value), 10)
    return 0.0 if rounded == 0 else rounded
