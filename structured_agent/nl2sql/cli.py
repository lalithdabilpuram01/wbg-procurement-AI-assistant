"""Run one natural-language question through the Claude-only SQL workflow.

LIVE: this makes a real Claude call through Portkey (and, with --execute plus --allow-dev-owner-credentials, a real
Databricks query). It refuses to run unless BOTH opt-ins are given for this invocation:
  * the --confirm-live-call flag, and
  * WBG_ALLOW_LIVE_LLM_CALLS=true in the process environment (never read from .env).
A configured .env alone therefore never triggers a call. The workflow always runs with the NeMo guardrails; if they
cannot be built (package missing or invalid configuration) the run is refused rather than run unguarded.

Usage (from procurement-intelligence/, MCP transport needs the isolated environment):
    WBG_ALLOW_LIVE_LLM_CALLS=true .venv-structured-mcp/bin/python -m structured_agent.nl2sql.cli --confirm-live-call \
        "How many bid invitations were published in FY2024?"
    ... --execute --allow-dev-owner-credentials      # also execute the validated SQL (labelled dev credentials)
    ... --transport in-process                       # call the service directly instead of an MCP server process
"""
from __future__ import annotations

import argparse
import dataclasses
import sys

from structured_agent.config import load_settings
from structured_agent.guardrails.pipeline import build_guardrails
from structured_agent.mcp_transport.client import WORKSPACE, server_parameters
from structured_agent.mcp_transport.session import BackgroundMcpSession
from structured_agent.nl2sql.live_guard import LiveCallsDisabled, authorize_live_access
from structured_agent.nl2sql.llm_client import PortkeyClaudeClient
from structured_agent.nl2sql.settings import ConfigurationError, load_claude_settings
from structured_agent.nl2sql.tools import InProcessToolCaller, McpToolCaller
from structured_agent.nl2sql.tracing import WorkflowTracer, configure_logfire, load_logfire_settings
from structured_agent.nl2sql.workflow import NlToSqlWorkflow
from structured_agent.observability import JsonlAuditSink, Telemetry


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Claude-only natural-language-to-SQL workflow (live call).")
    parser.add_argument("question")
    parser.add_argument("--confirm-live-call", action="store_true",
                        help="required: confirms that a real (billable) Claude request may be sent")
    parser.add_argument("--principal", default="analyst-demo", help="trusted principal for this run (deployment choice)")
    parser.add_argument("--transport", choices=["mcp", "in-process"], default="mcp")
    parser.add_argument("--execute", action="store_true", help="execute the SQL if the validator approves it")
    parser.add_argument("--allow-dev-owner-credentials", action="store_true",
                        help="permit the write-capable owner credential in labelled development mode")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        live_access = authorize_live_access(arguments.confirm_live_call)   # both opt-ins, before anything else
    except LiveCallsDisabled as error:
        print(f"Refusing to run: {error}", file=sys.stderr)
        return 3
    try:
        claude = PortkeyClaudeClient(load_claude_settings(), live_access=live_access)
    except ConfigurationError as error:
        print(error, file=sys.stderr)   # names variables only
        return 2
    try:
        guardrails = build_guardrails()   # fail closed: no unguarded live run
    except (ConfigurationError, ImportError) as error:
        print(f"Refusing to run without guardrails: {type(error).__name__}: {error}", file=sys.stderr)
        return 4

    settings = dataclasses.replace(load_settings(), allow_dev_owner_credentials=arguments.allow_dev_owner_credentials)
    telemetry = Telemetry(JsonlAuditSink(settings.audit_log_path))   # durable audit trail (always on)
    tracer = build_tracer()
    session = None
    if arguments.transport == "mcp":
        session = BackgroundMcpSession(server_parameters(arguments.principal,
                                                         allow_dev_execution=arguments.allow_dev_owner_credentials)).open()
        tools = McpToolCaller(session, arguments.principal)
    else:
        from structured_agent.service import StructuredDataService
        tools = InProcessToolCaller(StructuredDataService.from_environment(settings), arguments.principal, WORKSPACE)
    try:
        workflow = NlToSqlWorkflow(tools, claude, telemetry, tracer=tracer, guardrails=guardrails)
        result = workflow.run(arguments.question, execute=arguments.execute)
        print(result.model_dump_json(indent=1))
    finally:
        tracer.force_flush()
        if session is not None:
            session.close()
    return 0


def build_tracer() -> WorkflowTracer:
    """Logfire tracing; export only when LOGFIRE_SEND_TO_LOGFIRE=true. Problems disable tracing (never fail open)."""
    try:
        tracer = configure_logfire(load_logfire_settings())
    except ConfigurationError as error:
        print(f"Logfire tracing disabled: {error}", file=sys.stderr)
        return WorkflowTracer.disabled()
    print(f"Logfire tracing on (export to Logfire: {'yes' if tracer.export_enabled else 'no'})", file=sys.stderr)
    return tracer


if __name__ == "__main__":
    sys.exit(main())
