"""Thread-safe ring buffer holding the last N seconds of snapshots."""

from __future__ import annotations

import threading
from collections import deque

from .snapshot import Snapshot


class RingBuffer:
    def __init__(self, window_s: float = 30.0) -> None:
        if window_s <= 0:
            raise ValueError("window_s must be > 0")
        self.window_s = window_s
        self._items: deque[Snapshot] = deque()
        self._lock = threading.Lock()

    def append(self, snap: Snapshot) -> None:
        with self._lock:
            self._items.append(snap)
            limit = snap.t - self.window_s
            while self._items and self._items[0].t < limit:
                self._items.popleft()

    def latest(self) -> Snapshot | None:
        with self._lock:
            return self._items[-1] if self._items else None

    def last(self, seconds: float) -> list[Snapshot]:
        """Samples of the most recent ``seconds`` (oldest first)."""
        with self._lock:
            if not self._items:
                return []
            limit = self._items[-1].t - seconds
            return [s for s in self._items if s.t >= limit]

    def all(self) -> list[Snapshot]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
