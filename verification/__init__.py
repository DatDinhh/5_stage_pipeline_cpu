"""Standard-library verification support for the five-stage CPU."""

from .encoding import EncodingError, ProgramImage, assemble
from .iss import Event, ISS, ISSExecutionError, ISSState, SparseMemory
from .checker import (
    CompletionError,
    StateFormatError,
    StateMismatch,
    TraceFormatError,
    TraceIntegrityError,
    TraceMismatch,
    TraceRecord,
    VerificationError,
    VerificationResult,
    compare_state,
    compare_trace,
    parse_state,
    parse_trace,
    verify_run,
)

__all__ = [
    "EncodingError", "ProgramImage", "assemble", "Event", "ISS",
    "ISSExecutionError", "ISSState", "SparseMemory", "TraceRecord",
    "VerificationError", "TraceFormatError", "TraceIntegrityError",
    "TraceMismatch", "StateFormatError", "StateMismatch", "CompletionError",
    "VerificationResult", "parse_trace", "compare_trace", "parse_state",
    "compare_state", "verify_run",
]
