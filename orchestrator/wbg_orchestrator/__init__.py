"""Offline LangGraph supervisor-planner-executor engine (Milestone A). External tracing is disabled on import."""
from .safety import disable_external_tracing

disable_external_tracing()
