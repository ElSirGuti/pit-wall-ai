"""The speech output: one thread, one queue, a backend. Nothing in the telemetry path ever waits on it.

``say`` returns immediately. Messages play in order; ``interrupt=True`` (critical alerts) cuts what is being
said and goes first, dropping the queued messages that were waiting. Every message gets a
:class:`SpeechRecord` with the time it was handed over, the time the backend was told to speak and (when
the backend can tell) the time speech actually started: that is the latency that counts.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from .tts import TtsBackend, TtsUnavailable

log = logging.getLogger(__name__)
START_POLL_TIMEOUT_S = 0.5
_STOP = object()


@dataclass
class SpeechRecord:
    text: str
    interrupt: bool
    meta: dict = field(default_factory=dict)
    handed_t: float = 0.0  # say() was called
    called_t: float = 0.0  # backend.speak() was called
    started_t: float | None = None  # the backend reported that it is speaking
    ended_t: float | None = None

    @property
    def queue_ms(self) -> float:
        return (self.called_t - self.handed_t) * 1000.0

    @property
    def start_ms(self) -> float | None:
        """From say() to the first audio, in ms."""
        return None if self.started_t is None else (self.started_t - self.handed_t) * 1000.0


class SpeechOutput:
    def __init__(
        self,
        backend_factory: Callable[[], TtsBackend],
        *,
        clock=time.perf_counter,
        history: int = 200,
    ) -> None:
        self._factory = backend_factory
        self._clock = clock
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._backend: TtsBackend | None = None
        self._ready = threading.Event()
        self._speaking = threading.Event()
        self.records: deque[SpeechRecord] = deque(maxlen=history)
        self.error: str | None = None
        self.dropped = 0

    # -- public -----------------------------------------------------------------------------------
    def start(self, timeout: float = 5.0) -> bool:
        """Start the thread and wait until the backend is open. False (and ``error``) if it cannot."""
        self._thread = threading.Thread(target=self._run, name="speech", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.error is None and self._backend is not None

    @property
    def busy(self) -> bool:
        return self._speaking.is_set() or not self._q.empty()

    def say(self, text: str, *, interrupt: bool = False, meta: dict | None = None) -> SpeechRecord:
        rec = SpeechRecord(text=text, interrupt=interrupt, meta=meta or {}, handed_t=self._clock())
        if interrupt:  # a critical message goes first and alone
            while True:
                try:
                    old = self._q.get_nowait()
                except queue.Empty:
                    break
                if old is not _STOP:
                    self.dropped += 1
        self._q.put(rec)
        return rec

    def cancel(self) -> None:
        """Cut what is being said and drop what waits (the cut happens on the speech thread)."""
        self.say("", interrupt=True, meta={"rule": "cancel"})

    def stop(self, timeout: float = 2.0) -> None:
        self._q.put(_STOP)
        if self._thread is not None:
            self._thread.join(timeout)

    # -- thread -----------------------------------------------------------------------------------
    def _run(self) -> None:
        try:
            self._backend = self._factory()
            self._backend.open()
        except TtsUnavailable as exc:
            self.error = str(exc)
            log.warning("voice unavailable", extra={"reason": str(exc)})
            self._ready.set()
            return
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            log.exception("voice failed to start")
            self._ready.set()
            return
        self._ready.set()
        backend = self._backend
        try:
            while True:
                item = self._q.get()
                if item is _STOP:
                    break
                self._play(backend, item)
        finally:
            try:
                backend.stop()
                backend.close()
            except Exception:  # noqa: BLE001
                log.exception("closing the voice failed")

    def _play(self, backend: TtsBackend, rec: SpeechRecord) -> None:
        try:
            if not rec.text:  # a cancel: stop whatever is playing, say nothing
                backend.stop()
                return
            self._speaking.set()
            rec.called_t = self._clock()
            backend.speak(rec.text, interrupt=rec.interrupt)
            deadline = self._clock() + START_POLL_TIMEOUT_S
            while self._clock() < deadline:
                if backend.is_speaking():
                    rec.started_t = self._clock()
                    break
                time.sleep(0.001)
            while backend.is_speaking():
                if self._interrupted():
                    break
                time.sleep(0.01)
            rec.ended_t = self._clock()
        except Exception:  # noqa: BLE001 - one bad message must not kill the voice
            log.exception("speaking failed", extra={"text": rec.text})
        finally:
            self._speaking.clear()
            self.records.append(rec)

    def _interrupted(self) -> bool:
        """True when a critical message arrived while this one was speaking (the queue holds only it)."""
        try:
            head = self._q.queue[0]
        except IndexError:
            return False
        return isinstance(head, SpeechRecord) and head.interrupt
