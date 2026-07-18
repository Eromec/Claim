"""Domain-specific exceptions with safe, user-facing messages."""


class ClaimTraceError(Exception):
    """Base exception for expected ClaimTrace failures."""


class PDFParsingError(ClaimTraceError):
    """Raised when a PDF cannot be safely parsed into traceable text."""


class DocumentTooLargeError(ClaimTraceError):
    """Raised before an API call when a paper exceeds the configured budget."""


class AnalysisError(ClaimTraceError):
    """Raised when the model analysis cannot produce a valid result."""


class TraceabilityError(ClaimTraceError):
    """Raised when model output references content absent from the paper index."""

