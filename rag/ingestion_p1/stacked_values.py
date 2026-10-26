"""Promoted in Step 6D: the implementation now lives in rag/ingestion/stacked_values.py (re-exported unchanged)."""
from rag.ingestion.stacked_values import (CONNECTOR_END, DATE_LINE, NUMBER_LINE, TOP_TOLERANCE, VALUE_LINE, CellLines,  # noqa: F401
                                          CleanedCell, SplitResult, clean_cell_lines, is_value_line, is_wrap, split_stacked_row,
                                          stacked_boundaries)
