"""Request-scoped context variables."""
from contextvars import ContextVar

# Per-request correlation ID set by middleware — use in log messages instead of PII.
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")
