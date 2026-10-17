"""Text-to-speech backends behind one small interface, and the listing of voices and outputs.

* ``sapi``     the Windows voices (no network, no download). Any voice installed in Windows shows up here:
               add one in *Settings > Time & language > Speech*, or install a SAPI-compatible engine, and
               ``pit-wall voices`` lists it. The backend object is created INSIDE the speech thread (COM must
               be initialised in the thread that uses it).
* ``command``  any external synthesiser the user has (a neural voice such as Piper, a script...): a command
               template that writes a WAV, which is then played. No dependency is added for it.
* ``console``  prints (the test double). ``none`` says nothing.

The audio **output device** is chosen by name for ``sapi`` (``voice.output_device``), e.g. the headset.
"""

from __future__ import annotations

import gc
import logging
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

# SAPI SpeechVoiceSpeakFlags / RunningState
_ASYNC = 1
_PURGE_BEFORE_SPEAK = 2
_RUNNING_SPEAKING = 2
# primary language ids (the low 10 bits of the LCID that SAPI reports as a hex string)
_PRIMARY_LANG = {"es": 0x0A, "en": 0x09, "de": 0x07}


_PRONOUNCE = {"es": "pe ese i", "en": "P S I", "de": "pe es i"}


_COMMA = {"es": "coma", "de": "Komma"}
_UNIT = {"es": {"s": "segundos"}, "de": {"s": "Sekunden"}}


def pronounce(text: str, language: str | None) -> str:
    """What the synthesiser should read.

    SAPI voices say "psi" as a word ("sai"), so it is spelled out, and a decimal comma is read as a clock time
    ("14,0 metros" came out as "14 horas 0 minutos"), so the comma is said: "14 coma 0 metros". A lap time
    "1:24,889" becomes "1 minuto, 24 coma 889".
    """
    lang = (language or "es")[:2].lower()
    spelled = _PRONOUNCE.get(lang, "P S I")
    text = re.sub(r"(?<![A-Za-z])psi(?![A-Za-z])", spelled, text, flags=re.IGNORECASE)
    comma = _COMMA.get(lang)
    if comma is None:
        return text
    text = re.sub(
        r"(\d+):(\d{2}),(\d+)",
        lambda m: (
            f"{m.group(1)} {'minuto' if m.group(1) == '1' else 'minutos'}, "
            f"{int(m.group(2))} {comma} {m.group(3)}"
            if lang == "es"
            else f"{m.group(1)} Minuten, {int(m.group(2))} {comma} {m.group(3)}"
        ),
        text,
    )
    text = re.sub(r"(\d+),(\d+)", lambda m: f"{m.group(1)} {comma} {m.group(2)}", text)
    # "4 coma 6 s" would be read "ese": a lone "s" after a number is seconds
    unit = _UNIT[lang]
    return re.sub(r"(?<=\d)\s+s(?![A-Za-z])", f" {unit['s']}", text)


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


@dataclass(frozen=True)
class VoiceInfo:
    name: str
    language: str  # "es", "en" or the raw LCID in hex when unknown
    gender: str = ""
    vendor: str = ""

    def line(self) -> str:
        bits = [self.language, self.gender, self.vendor]
        return f"{self.name}  ({', '.join(b for b in bits if b)})"


def _lang_of(lcid_hex: str) -> str:
    try:
        primary = int(lcid_hex.split(";")[0], 16) & 0x3FF
    except (ValueError, AttributeError):
        return lcid_hex or "?"
    for code, pid in _PRIMARY_LANG.items():
        if primary == pid:
            return code
    return f"lcid {lcid_hex}"


def _sapi():
    if sys.platform != "win32":
        raise TtsUnavailable("the sapi backend is Windows only")
    try:
        import pythoncom
        import win32com.client
    except ImportError as exc:
        raise TtsUnavailable('pywin32 is not installed: pip install "pit-wall-ai[voice]"') from exc
    return pythoncom, win32com.client


def _com_call(fn):
    """Run ``fn(client)`` with COM initialised, and drop every COM object before COM is shut down."""
    pythoncom, client = _sapi()
    pythoncom.CoInitialize()
    try:
        return fn(client)  # the locals of ``fn`` (the COM objects) are gone when it returns
    finally:
        gc.collect()
        pythoncom.CoUninitialize()


def list_sapi_voices() -> list[VoiceInfo]:
    """The voices Windows exposes to SAPI, as installed (not only the ones this project prefers)."""

    def read(client) -> list[VoiceInfo]:
        vs = client.Dispatch("SAPI.SpVoice").GetVoices()
        out = []
        for i in range(vs.Count):
            v = vs.Item(i)
            out.append(
                VoiceInfo(
                    name=v.GetAttribute("Name"),
                    language=_lang_of(v.GetAttribute("Language")),
                    gender=_safe_attr(v, "Gender"),
                    vendor=_safe_attr(v, "Vendor"),
                )
            )
        return out

    return _com_call(read)


def list_sapi_outputs() -> list[str]:
    """The audio output devices SAPI can speak through (descriptions, in the order SAPI lists them)."""

    def read(client) -> list[str]:
        outs = client.Dispatch("SAPI.SpVoice").GetAudioOutputs()
        return [outs.Item(i).GetDescription() for i in range(outs.Count)]

    return _com_call(read)


def _safe_attr(token, name: str) -> str:
    try:
        return str(token.GetAttribute(name) or "")
    except Exception:  # noqa: BLE001 - an engine without that attribute
        return ""


_NOISE_WORDS = {"microsoft", "desktop", "mobile", "online", "natural", "voice", "text-to-speech"}


def short_name(voice_name: str) -> str:
    """The given name of a voice: "Microsoft Sabina Desktop - Spanish (Mexico)" -> "Sabina"."""
    head = voice_name.split(" - ")[0]
    for word in head.split():
        if word.lower() not in _NOISE_WORDS:
            return word
    return voice_name


def synth_to_wav(
    text: str, path, *, voice_name: str | None = None, language: str = "es", rate: int = 0
) -> None:
    """Render ``text`` to a 16 kHz mono 16-bit WAV with a SAPI voice (no playback)."""
    voice_name = voice_name or None

    def run(client) -> None:
        voice = client.Dispatch("SAPI.SpVoice")
        chosen = None
        for i in range(voice.GetVoices().Count):
            v = voice.GetVoices().Item(i)
            if voice_name and voice_name.lower() in v.GetAttribute("Name").lower():
                chosen = v
                break
            try:
                lcid = int(v.GetAttribute("Language").split(";")[0], 16)
            except (ValueError, AttributeError):
                continue
            if chosen is None and (lcid & 0x3FF) == _PRIMARY_LANG.get(language, -1):
                chosen = v
        if chosen is not None:
            voice.Voice = chosen
        voice.Rate = rate
        stream = client.Dispatch("SAPI.SpFileStream")
        stream.Format.Type = 22  # SAFT16kHz16BitMono
        stream.Open(str(path), 3)  # SSFMCreateForWrite
        voice.AudioOutputStream = stream
        voice.Speak(text, 0)
        stream.Close()

    _com_call(run)


def pick_by_name(names: list[str], query: str | None) -> str | None:
    """The first name containing the query (case-insensitive); ``None`` when there is no query."""
    if not query:
        return None
    q = query.lower().strip()
    for n in names:
        if q in n.lower():
            return n
    raise TtsUnavailable(f"nothing matches {query!r}; available: {', '.join(names) or 'none'}")


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
        output_device: str | None = None,
    ) -> None:
        self.language, self.voice_name = language, voice_name
        self.rate, self.volume = rate, volume
        self.output_device = output_device
        self._voice = None
        self.voice_used = ""
        self.output_used = ""
        self._pythoncom = None

    def open(self) -> None:
        pythoncom, client = _sapi()
        pythoncom.CoInitialize()
        self._pythoncom = pythoncom
        try:
            voice = client.Dispatch("SAPI.SpVoice")
        except Exception as exc:  # noqa: BLE001
            raise TtsUnavailable(f"SAPI is not available: {exc}") from exc
        chosen = self._pick(voice)
        if chosen is not None:
            voice.Voice = chosen
        if self.output_device:
            outs = voice.GetAudioOutputs()
            names = [outs.Item(i).GetDescription() for i in range(outs.Count)]
            found = pick_by_name(names, self.output_device)
            voice.AudioOutput = outs.Item(names.index(found))
            self.output_used = found or ""
        voice.Rate, voice.Volume = self.rate, self.volume
        self._voice = voice
        self.voice_used = voice.Voice.GetAttribute("Name")
        log.info(
            "voice ready",
            extra={
                "voice": self.voice_used,
                "rate": self.rate,
                "output": self.output_used or "default",
            },
        )

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
        self._voice.Speak(pronounce(text, self.language), flags)

    def is_speaking(self) -> bool:
        if self._voice is None:
            return False
        return self._voice.Status.RunningState == _RUNNING_SPEAKING

    def stop(self) -> None:
        if self._voice is not None:
            self._voice.Speak("", _ASYNC | _PURGE_BEFORE_SPEAK)

    def close(self) -> None:
        self._voice = None
        gc.collect()  # release the COM objects while COM is still initialised
        if self._pythoncom is not None:
            self._pythoncom.CoUninitialize()
            self._pythoncom = None


class CommandTts:
    """Any synthesiser that can write a WAV from a command line.

    ``command`` is a template with ``{text}`` and ``{out}``; the text is passed as one argument (never through
    a shell). An engine that reads the text from standard input uses ``{stdin}`` in place of ``{text}``,
    for example ``piper --model C:\\voices\\es.onnx --output_file {out} {stdin}``. The WAV is played with
    the Windows sound API, so no audio library is needed.
    """

    name = "command"

    def __init__(self, command: str, *, timeout_s: float = 15.0) -> None:
        if "{out}" not in command:
            raise TtsUnavailable("voice.command must contain {out}, the WAV file the engine writes")
        self.command = command
        self.timeout_s = timeout_s
        self._until = 0.0
        self._tmp = tempfile.mkdtemp(prefix="pitwall-tts-")
        self._n = 0

    def open(self) -> None:
        if sys.platform != "win32":
            raise TtsUnavailable("the command backend plays through the Windows sound API")

    def speak(self, text: str, *, interrupt: bool = False) -> None:
        import winsound

        self._n += 1
        out = os.path.join(self._tmp, f"u{self._n % 8}.wav")
        stdin = None
        argv = []
        for part in shlex.split(self.command, posix=False):
            part = part.strip('"')
            if part == "{stdin}":
                stdin = text
                continue
            argv.append(part.replace("{out}", out).replace("{text}", text))
        try:
            subprocess.run(  # noqa: S603 - the command is the user's own configuration
                argv,
                input=stdin,
                text=True,
                check=True,
                capture_output=True,
                timeout=self.timeout_s,
            )
            with wave.open(out, "rb") as w:
                dur = w.getnframes() / float(w.getframerate())
        except (OSError, subprocess.SubprocessError, wave.Error) as exc:
            raise TtsUnavailable(f"the voice command failed: {exc}") from exc
        self._until = time.perf_counter() + dur
        flags = winsound.SND_FILENAME | winsound.SND_ASYNC
        winsound.PlaySound(out, flags)

    def is_speaking(self) -> bool:
        return time.perf_counter() < self._until

    def stop(self) -> None:
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        self._until = 0.0

    def close(self) -> None:
        self.stop()


def create_backend(
    kind: str,
    *,
    language: str,
    voice_name: str | None,
    rate: int,
    volume: int,
    output_device: str | None = None,
    command: str | None = None,
) -> TtsBackend:
    if kind == "sapi":
        return SapiTts(
            language=language,
            voice_name=voice_name,
            rate=rate,
            volume=volume,
            output_device=output_device,
        )
    if kind == "command":
        if not command:
            raise TtsUnavailable("voice.backend is 'command' but voice.command is empty")
        return CommandTts(command)
    if kind == "console":
        return ConsoleTts()
    if kind == "none":
        return NullTts()
    raise TtsUnavailable(f"unknown voice backend {kind!r}")
