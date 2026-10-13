"""Text-to-speech backends behind one small interface.

``sapi`` uses the Windows voices (no network, no download). The backend object is created INSIDE the speech
thread (COM must be initialised in the thread that uses it). ``console`` prints (and is the test double).
Piper or a cloud voice can be added later by implementing :class:`TtsBackend`.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Protocol

log = logging.getLogger(__name__)

# SAPI SpeechVoiceSpeakFlags / RunningState
_ASYNC = 1
_PURGE_BEFORE_SPEAK = 2
_RUNNING_SPEAKING = 2
# primary language ids (the low 10 bits of the LCID that SAPI reports as a hex string)
_PRIMARY_LANG = {"es": 0x0A, "en": 0x09}


class TtsBackend(Protocol):
    name: str

    def open(self) -> None: ...

    def speak(self, text: str, *, interrupt: bool = False) -> None:
        """Start speaking and return immediately. ``interrupt`` cuts what is being said."""

    def is_speaking(self) -> bool: ...

    def stop(self) -> None: ...

    def close(self) -> None: ...


class TtsUnavailable(RuntimeError):
    """The requested backend cannot run on this machine (not Windows, pywin32 missing, no voice)."""


class ConsoleTts:
    """Prints the message. ``speaking_for_s`` fakes a speaking duration so the queue logic can be exercised."""

    name = "console"

    def __init__(
        self, *, speaking_for_s: float = 0.0, stream=None, clock=time.perf_counter
    ) -> None:
        self.speaking_for_s = speaking_for_s
        self._stream = stream or sys.stdout
        self._clock = clock
        self._until = 0.0
        self.spoken: list[str] = []

    def open(self) -> None:
        pass

    def speak(self, text: str, *, interrupt: bool = False) -> None:
        self.spoken.append(text)
        self._until = self._clock() + self.speaking_for_s
        print(f"[voice] {text}", file=self._stream, flush=True)

    def is_speaking(self) -> bool:
        return self._clock() < self._until

    def stop(self) -> None:
        self._until = 0.0

    def close(self) -> None:
        pass


class NullTts(ConsoleTts):
    name = "none"

    def speak(self, text: str, *, interrupt: bool = False) -> None:
        self.spoken.append(text)


class SapiTts:
    """Windows SAPI 5 through pywin32."""

    name = "sapi"

    def __init__(
        self,
        *,
        language: str = "es",
        voice_name: str | None = None,
        rate: int = 0,
        volume: int = 100,
    ) -> None:
        self.language, self.voice_name = language, voice_name
        self.rate, self.volume = rate, volume
        self._voice = None
        self.voice_used = ""
        self._pythoncom = None

    def open(self) -> None:
        if sys.platform != "win32":
            raise TtsUnavailable("the sapi backend is Windows only")
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise TtsUnavailable(
                'pywin32 is not installed: pip install "pit-wall-ai[voice]"'
            ) from exc
        pythoncom.CoInitialize()
        self._pythoncom = pythoncom
        try:
            voice = win32com.client.Dispatch("SAPI.SpVoice")
        except Exception as exc:  # noqa: BLE001
            raise TtsUnavailable(f"SAPI is not available: {exc}") from exc
        chosen = self._pick(voice)
        if chosen is not None:
            voice.Voice = chosen
        voice.Rate, voice.Volume = self.rate, self.volume
        self._voice = voice
        self.voice_used = voice.Voice.GetAttribute("Name")
        log.info("voice ready", extra={"voice": self.voice_used, "rate": self.rate})

    def _pick(self, voice):
        voices = voice.GetVoices()
        wanted = (self.voice_name or "").lower()
        by_lang = None
        for i in range(voices.Count):
            v = voices.Item(i)
            name = v.GetAttribute("Name")
            if wanted and wanted in name.lower():
                return v
            try:
                lcid = int(v.GetAttribute("Language").split(";")[0], 16)
            except (ValueError, AttributeError):
                continue
            if by_lang is None and (lcid & 0x3FF) == _PRIMARY_LANG.get(self.language, -1):
                by_lang = v
        if wanted:
            log.warning(
                "voice not found, using the first of the language", extra={"wanted": wanted}
            )
        if by_lang is None:
            log.warning(
                "no voice for the language, using the default", extra={"language": self.language}
            )
        return by_lang

    def speak(self, text: str, *, interrupt: bool = False) -> None:
        if self._voice is None:
            raise TtsUnavailable("voice not open")
        flags = _ASYNC | (_PURGE_BEFORE_SPEAK if interrupt else 0)
        self._voice.Speak(text, flags)

    def is_speaking(self) -> bool:
        if self._voice is None:
            return False
        return self._voice.Status.RunningState == _RUNNING_SPEAKING

    def stop(self) -> None:
        if self._voice is not None:
            self._voice.Speak("", _ASYNC | _PURGE_BEFORE_SPEAK)

    def close(self) -> None:
        self._voice = None
        if self._pythoncom is not None:
            self._pythoncom.CoUninitialize()
            self._pythoncom = None


def create_backend(
    kind: str, *, language: str, voice_name: str | None, rate: int, volume: int
) -> TtsBackend:
    if kind == "sapi":
        return SapiTts(language=language, voice_name=voice_name, rate=rate, volume=volume)
    if kind == "console":
        return ConsoleTts()
    if kind == "none":
        return NullTts()
    raise TtsUnavailable(f"unknown voice backend {kind!r}")
