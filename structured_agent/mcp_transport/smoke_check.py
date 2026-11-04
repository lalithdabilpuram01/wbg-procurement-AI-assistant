"""End-to-end MCP smoke check: start the server over stdio, discover the tools and call each of the seven once.

Usage (from procurement-intelligence/):
    .venv-structured-mcp/bin/python -m structured_agent.mcp_transport.smoke_check            # execution refused
    .venv-structured-mcp/bin/python -m structured_agent.mcp_transport.smoke_check --execute  # labelled dev execution

Without --execute the server keeps its default-deny behaviour, so execute_readonly_sql must return
CREDENTIAL_NOT_READ_ONLY. With --execute the server is started with WBG_ALLOW_DEV_OWNER_CREDENTIALS=true and the result
must say credential_mode=dev_owner_credentials, production_read_only=false.
"""
from __future__ import annotations

import argparse
import asyncio
import time

from structured_agent.mcp_transport.client import call_tool, connect, server_parameters

GOLD = "workspace.wbg_gold"
CONTRACT_SQL = (f"SELECT wb_contract_number, procurement_method, contract_amount_usd FROM {GOLD}.gold_contract_analytics "
                "WHERE wb_contract_number = :contract LIMIT 5")

TOOL_CALLS = [
    ("search_table_registry", {"query": "contract award value by procurement method", "top_k": 3}),
    ("get_table_schema", {"object_fqn": f"{GOLD}.gold_contract_analytics"}),
    ("get_table_relationships", {"object_fqn": f"{GOLD}.gold_contract_analytics"}),
    ("get_metric_definition", {"metric_id": "contract_award_value_usd"}),
    ("get_verified_sql_examples", {"object_fqn": f"{GOLD}.gold_contract_analytics", "limit": 2}),
    ("validate_sql", {"sql": CONTRACT_SQL, "parameters": {"contract": "1803814"}}),
    ("execute_readonly_sql", {"sql": CONTRACT_SQL, "parameters": {"contract": "1803814"}}),
]


def summarise(tool: str, response: dict) -> str:
    """One readable line per tool response (no result values beyond the row count)."""
    errors = ",".join(e["code"] for e in response.get("errors", [])) or "-"
    detail = {
        "search_table_registry": lambda r: f"top: {[c['object_fqn'].split('.')[-1] for c in r['candidates']]}",
        "get_table_schema": lambda r: f"{len(r['columns'])} authorized columns, {r['hidden_column_count']} hidden",
        "get_table_relationships": lambda r: f"{len(r['relationships'])} approved joins",
        "get_metric_definition": lambda r: f"{[m['expression'] for m in r['metrics']]}",
        "get_verified_sql_examples": lambda r: f"{[(e['example_id'], e['verification_status']) for e in r['examples']]}",
        "validate_sql": lambda r: f"approved={r['report']['approved'] if r.get('report') else None}",
        "execute_readonly_sql": lambda r: f"rows={r['row_count']} provenance={r.get('provenance')}",
    }[tool](response)
    return f"{tool:28s} status={response['status']:10s} errors={errors:26s} {detail}"


async def run(execute: bool) -> None:
    """Connect, list the tools and call each of the seven once, printing one summary line per call."""
    started = time.perf_counter()
    async with connect(server_parameters(allow_dev_execution=execute)) as session:
        print(f"connected and initialized in {time.perf_counter() - started:.1f} s")
        tools = [t.name for t in (await session.list_tools()).tools]
        print(f"tools ({len(tools)}): {tools}")
        for tool, arguments in TOOL_CALLS:
            response = await call_tool(session, tool, {**arguments, "request_id": f"smoke_{tool}"})
            print(summarise(tool, response))
    print("session closed cleanly")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execute", action="store_true", help="enable labelled development execution in the server")
    asyncio.run(run(parser.parse_args().execute))


if __name__ == "__main__":
    main()
