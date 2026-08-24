"""The boundary between this app and any phone provider.

Everything above this line reasons about invoices and promises. Everything below
it reasons about calls. The engine depends on `CallPort`, so the same run works
against CALL-E or against the local simulator without changing a decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from kept.models import CallCycle


class CallPlacementError(RuntimeError):
    """Raised when a call could not be placed or its result could not be read."""

    def __init__(self, message: str, *, code: str = "call_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CallRequest:
    task: str
    phone: str
    region: str
    locale: str
    result_schema: dict[str, Any]
    idempotency_key: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlacedCall:
    """The provider's terminal answer, still free of any business meaning."""

    call_id: str
    status: str
    task_completed: bool | None
    confidence: float
    structured_result: dict[str, Any] | None
    summary: str | None
    failure_code: str | None = None
    transcript: tuple[tuple[str, str], ...] = ()

    @property
    def is_terminal_success(self) -> bool:
        return self.status == "completed"


class CallPort(Protocol):
    def dispatch(self, request: CallRequest) -> str:
        """Start the call and return its id without waiting for the outcome."""
        ...

    def await_result(self, call_id: str) -> PlacedCall:
        """Poll an already-started call until it reaches a terminal state."""
        ...


def idempotency_key(*, invoice_id: str, cycle: CallCycle, attempt: int) -> str:
    """Stable across retries and restarts, so a crash never dials twice.

    The key is derived from durable business identity rather than from run time,
    which is what makes replaying an interrupted run safe.
    """
    return f"kept:{invoice_id}:{cycle.value}:{attempt}"
