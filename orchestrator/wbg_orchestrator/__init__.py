"""The app: web API, background jobs and the LangGraph investigation engine (supervisor -> planner -> workers ->
verification -> answer). Start reading at api.py (HTTP), then app_service.py (jobs), conversation_bridge.py (routing)
and graph.py (the investigation steps). LangSmith tracing is disabled on import; Logfire tracing is in observability.py."""
from .safety import disable_external_tracing

disable_external_tracing()
