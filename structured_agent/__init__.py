"""The Structured Data Agent: questions over the Gold tables answered with validated, read-only SQL.

    registry/        which tables, columns, metrics and joins exist (the Table Registry)
    nl2sql/          question -> SQL with Claude, behind guardrails
    sql_validation/  every statement is checked before it runs (read-only, known objects, row limits)
    execution/       runs the approved SQL on the Databricks SQL warehouse
    mcp_server.py    exposes the seven approved operations over MCP (the app's default transport)
"""
