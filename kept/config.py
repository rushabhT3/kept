"""Operator-owned settings. Defaults are the cautious end of every choice."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api.heycall-e.com"


@dataclass(frozen=True, slots=True)
class Policy:
    """Rules that decide who may be called, and when a word becomes a record."""

    grace_days_after_due: int = 3
    quiet_hours_start: int = 20
    quiet_hours_end: int = 9
    min_days_between_calls: int = 7
    promise_grace_days: int = 2
    min_confidence_to_record: float = 0.7
    max_promise_horizon_days: int = 45
    max_calls_per_run: int = 5

    def __post_init__(self) -> None:
        if not 0 <= self.quiet_hours_start <= 23 or not 0 <= self.quiet_hours_end <= 23:
            raise ValueError("Quiet hours must be hours of a day.")
        if not 0.0 <= self.min_confidence_to_record <= 1.0:
            raise ValueError("min_confidence_to_record must be between 0 and 1.")
        if self.max_calls_per_run < 0:
            raise ValueError("max_calls_per_run cannot be negative.")


@dataclass(frozen=True, slots=True)
class Organisation:
    """Who the call is made on behalf of. Read out in full on every call."""

    name: str
    callback_number: str
    agent_name: str = "an automated assistant"

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Organisation name is required; calls must identify the creditor.")
        if not self.callback_number.startswith("+"):
            raise ValueError("Organisation callback_number must be E.164.")


@dataclass(frozen=True, slots=True)
class Credentials:
    api_key: str
    base_url: str = DEFAULT_BASE_URL


class MissingCredentialsError(RuntimeError):
    """Raised when a live run is requested without an API key."""


def load_credentials(environ: dict[str, str] | None = None) -> Credentials:
    source = os.environ if environ is None else environ
    api_key = source.get("CALLE_API_KEY", "").strip()
    if not api_key:
        raise MissingCredentialsError(
            "CALLE_API_KEY is not set. Live calls are refused without it; "
            "run with --simulate to exercise the same code path for free."
        )
    return Credentials(api_key=api_key, base_url=source.get("CALLE_BASE_URL", DEFAULT_BASE_URL))
