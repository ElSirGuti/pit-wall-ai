"""The contract every telemetry source fulfils (live game, replay, test double)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .snapshot import Snapshot


class SourceUnavailable(RuntimeError):
    """The source cannot deliver data right now (e.g. Assetto Corsa is not running). Retry later."""


class SourceExhausted(RuntimeError):
    """A finite source (a replayed log) reached its end."""


@runtime_checkable
class TelemetrySource(Protocol):
    name: str

    def open(self) -> None:
        """Connect. Raises :class:`SourceUnavailable` if it cannot (yet)."""

    def read(self) -> Snapshot | None:
        """The newest sample, or ``None`` when nothing new arrived since the last call.

        Must be cheap and never block: the poller calls it 30-60 times per second.
        Raises :class:`SourceUnavailable` if the data went away, :class:`SourceExhausted` at the end.
        """

    def close(self) -> None:
        """Release resources. Safe to call twice."""
