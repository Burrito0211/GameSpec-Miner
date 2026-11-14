"""Trace reading and schema validation (SPEC §5.2)."""

from rulelens.io.trace import Trace, TraceError, read_trace, validate_trace

__all__ = ["Trace", "TraceError", "read_trace", "validate_trace"]
