"""Databricks query executor behind a small interface.

`SqlExecutor` is what the gateway depends on. `DatabricksSparkExecutor` runs SQL through Databricks Connect with a
CLI profile. A dedicated read-only principal replaces the current credential by pointing WBG_DATABRICKS_PROFILE at its
profile: the SQL Agent code does not change.

The executor never decides what may run: it only receives SQL that the gateway has validated.
"""
from __future__ import annotations

import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol


class QueryTimeout(Exception):
    pass


@dataclass(frozen=True)
class CredentialCheck:
    write_capable: bool
    evidence: str            # counts only; no identity is recorded


@dataclass
class RawResult:
    columns: list[tuple[str, str]]       # (name, Spark type)
    rows: list[dict[str, Any]]


class SqlExecutor(Protocol):
    """What the gateway needs from an engine: a credential check and a bounded query run."""

    def check_credential(self) -> CredentialCheck: ...

    def execute(self, sql: str, parameters: dict[str, Any], timeout_seconds: int) -> RawResult: ...


def to_json_value(value: Any) -> Any:
    """Exact, JSON-safe values: decimals as strings (no float rounding), dates as ISO text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [to_json_value(v) for v in value]
    return value


class DatabricksSparkExecutor:
    """Runs validated SQL on Databricks through Databricks Connect, one query at a time."""

    def __init__(self, profile: str):
        """Keep the Databricks profile; the Spark session is created on first use."""
        self._profile = profile
        self._spark = None
        self._lock = threading.Lock()
        # One worker thread: queries run one at a time, and the caller can stop waiting at the timeout.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="databricks-query")

    def _session(self):
        """Create the Spark session on first use (thread-safe) and reuse it afterwards."""
        with self._lock:
            if self._spark is None:
                from silver.common.session import get_spark

                self._spark = get_spark(self._profile)
            return self._spark

    def check_credential(self) -> CredentialCheck:
        """Write-capable if the identity owns, or holds a write privilege on, any object in the approved schemas."""
        spark = self._session()
        owned = spark.sql("""
            SELECT count(*) AS n FROM workspace.information_schema.tables
            WHERE table_schema IN ('wbg_silver', 'wbg_gold') AND table_owner = current_user()""").collect()[0]["n"]
        privileged = spark.sql("""
            SELECT count(*) AS n FROM workspace.information_schema.table_privileges
            WHERE table_schema IN ('wbg_silver', 'wbg_gold') AND grantee = current_user()
              AND privilege_type IN ('ALL PRIVILEGES', 'MODIFY', 'MANAGE', 'APPLY TAG')""").collect()[0]["n"]
        return CredentialCheck(write_capable=owned > 0 or privileged > 0,
                               evidence=f"owns {owned} approved objects; {privileged} write privileges")

    def execute(self, sql: str, parameters: dict[str, Any], timeout_seconds: int) -> RawResult:
        """Run the SQL with bound parameters; raises QueryTimeout when it exceeds the time limit."""
        spark = self._session()

        def run() -> RawResult:
            """Run the query and collect its columns (name, type) and its rows as JSON-ready values."""
            frame = spark.sql(sql, args=parameters or None)
            columns = [(f.name, f.dataType.simpleString()) for f in frame.schema.fields]
            rows = [{k: to_json_value(v) for k, v in row.asDict().items()} for row in frame.collect()]
            return RawResult(columns, rows)

        future = self._pool.submit(run)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout:
            spark.interruptAll()  # cancel the running query on the server
            raise QueryTimeout(f"Query exceeded {timeout_seconds} s and was interrupted") from None
