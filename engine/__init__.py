"""CWCCC MC OMR marking engine (no LLM)."""

from .pipeline import mark_pdf, MarkingResult
from .scheme import parse_marking_scheme, DEFAULT_KEY

__all__ = ["mark_pdf", "MarkingResult", "parse_marking_scheme", "DEFAULT_KEY"]
