"""Shared pytest settings for every suite under tests/.

tests/<component>/  offline tests for one component (no network, no Claude, no Databricks); `uv run wbg-test` runs them.
tests/live/         tests that call Databricks, Lakebase, Claude or the MCP server. They cost money or touch real
                    systems, so they are never collected unless WBG_RUN_LIVE_TESTS=1 is set for that one command:

                        WBG_RUN_LIVE_TESTS=1 uv run pytest tests/live/<suite>
"""
from __future__ import annotations

import os

collect_ignore = [] if os.environ.get("WBG_RUN_LIVE_TESTS") == "1" else ["live"]
