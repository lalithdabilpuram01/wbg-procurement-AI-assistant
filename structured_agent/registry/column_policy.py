"""Applies curated/column_policy.json to a column name: classification, unit, measure family and additivity."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnPolicy:
    """The policy of one column, as stored on RegisteredColumn."""

    classification: str
    policy_source: str
    unit: str | None
    measure_family: str | None
    additive: bool


class ColumnPolicyRules:
    """The rules of curated/column_policy.json, compiled once and applied per column."""

    def __init__(self, config: dict):
        """Compile the classification rules and the unit and measure-family patterns from the config."""
        self._rules = config["rules"]
        self._units = [(re.compile(u["pattern"]), u["unit"]) for u in config["units"]]
        self._families = [(re.compile(f["pattern"]), f["family"]) for f in config["measure_families"]]
        self._non_additive = re.compile(config["non_additive_pattern"])
        self.default_source = config["classifications"]["public"]["source"]

    def classify(self, column: str) -> tuple[str, str]:
        """First matching rule wins; columns not named by any sensitive rule are public."""
        for rule in self._rules:
            if column in rule.get("columns", []) or ("pattern" in rule and re.search(rule["pattern"], column)):
                return rule["classification"], rule["source"]
        return "public", self.default_source

    def unit(self, column: str, data_type: str) -> str | None:
        """Unit of a numeric column from the first matching name pattern; None for non-numeric columns."""
        if not data_type.startswith(("decimal", "bigint", "int", "double", "float")):
            return None
        return next((unit for pattern, unit in self._units if pattern.search(column)), None)

    def measure_family(self, column: str) -> str | None:
        return next((family for pattern, family in self._families if pattern.search(column)), None)

    def additive(self, column: str, data_type: str) -> bool:
        numeric = data_type.startswith(("decimal", "bigint", "int", "double", "float"))
        return numeric and not self._non_additive.search(column)

    def policy_for(self, column: str, data_type: str) -> ColumnPolicy:
        """Full policy for one column: classification, unit, measure family and additivity."""
        classification, source = self.classify(column)
        return ColumnPolicy(classification, source, self.unit(column, data_type), self.measure_family(column),
                            self.additive(column, data_type))
