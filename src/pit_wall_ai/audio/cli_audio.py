"""``pit-wall devices``, ``mic-test`` and ``voices``: pick the microphone and the voice by ear and by name."""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable

from ..config import AppConfig
from ..home import home_dir
from ..voice import tts as T
from .capture import AudioBackend, Microphone, UtteranceRecorder, write_wav
from .devices import DeviceNotFound, find_device, usable

SAMPLE = {
    "es": "Hola, soy {name} y seré tu ingeniero de pista. Quedan dos vueltas de gasolina.",
    "en": "Hello, I am {name} and I will be your race engineer. Two laps of fuel left.",
    "de": "Hallo, ich bin {name} und werde dein Renningenieur sein. Sprit für zwei Runden.",
}


def _backend() -> AudioBackend:
    try:
        from .capture import SoundDeviceBackend

        return SoundDeviceBackend()
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit('the audio library is missing: pip install -e ".[voice]"') from exc


def cmd_devices(
    cfg: AppConfig, a: argparse.Namespace, *, backend: AudioBackend | None = None
) -> int:
    backend = backend or _backend()
    devices = backend.devices()
    chosen_in = cfg.audio.input_device
    print("Microphones (use a part of the name in audio.input_device):")
    for d in usable(devices, kind="input"):
        mark = "*" if chosen_in and chosen_in.lower() in d.name.lower() else " "
        warn = (
            "   <- telephone quality (8 kHz): poor for speech recognition" if d.narrowband else ""
        )
        print(f" {mark} {d.name}  [{d.hostapi}, {d.default_rate:.0f} Hz]{warn}")
    print("\nAudio outputs the system sees:")
    for d in usable(devices, kind="output"):
        print(f"   {d.name}  [{d.hostapi}]")
    try:
        outs = T.list_sapi_outputs()
        print(
            "\nOutputs the voice can use (use a part of the name in engineer.voice.output_device):"
        )
        for o in outs:
            mark = (
                "*"
                if cfg.engineer.voice.output_device
                and cfg.engineer.voice.output_device.lower() in o.lower()
                else " "
            )
            print(f" {mark} {o}")
    except T.TtsUnavailable as exc:
        print(f"\n(voice outputs not available: {exc})")
    return 0


def cmd_mic_test(
    cfg: AppConfig,
    a: argparse.Namespace,
    *,
    backend: AudioBackend | None = None,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    backend = backend or _backend()
    query = a.device or cfg.audio.input_device
    try:
        dev, others = find_device(backend.devices(), query, kind="input")
    except DeviceNotFound as exc:
        out(str(exc))
        return 2
    label = dev.name if dev else "the system default microphone"
    out(
        f"microphone: {label}"
        + (f"  (also matched: {', '.join(d.name for d in others)})" if others else "")
    )
    if dev and dev.narrowband:
        out(
            "  warning: this device works at 8 kHz (a Bluetooth headset in hands-free mode): speech "
            "recognition will be poor. Use a wired or USB microphone, or the headset's high-quality mode."
        )
    got: list = []
    rec = UtteranceRecorder(
        on_utterance=got.append,
        preroll_ms=cfg.audio.preroll_ms,
        tail_ms=cfg.audio.tail_ms,
        min_ms=1,
        silence_rms=0.0,
    )
    mic = Microphone(backend, dev, rec)
    try:
        mic.start()
    except OSError as exc:
        out(f"cannot open the microphone: {exc}")
        return 1
    out(f"recording {a.seconds:g} s at {mic.opened_rate} Hz: speak now")
    rec.press()
    t_end = time.monotonic() + a.seconds
    while time.monotonic() < t_end:
        bar = "#" * min(40, int(mic.level * 200))
        out(f"  {mic.level * 100:5.1f} %  {bar}")
        sleep(0.25)
    rec.release()
    sleep(cfg.audio.tail_ms / 1000 + 0.1)
    mic.stop()
    if not got:
        out("nothing was recorded")
        return 1
    u = got[0]
    out(
        f"recorded {u.duration_s:.2f} s, peak {u.peak * 100:.0f} %, average level {u.rms * 100:.1f} %"
    )
    if u.peak < 0.02:
        out(
            "  the level is very low or silent: check the microphone selection and the Windows input volume"
        )
    elif u.peak > 0.98:
        out("  the signal clips: lower the microphone gain")
    if a.out:
        write_wav(a.out, u)
        out(f"saved to {a.out}")
    return 0


def _fixed_phrases(lang: str) -> list[str]:
    """The sentences that never change: the fixed alert texts, the radio phrases, the milestone variants."""
    from ..engineer import messages as M
    from ..engineer.milestones import VARIANTS
    from ..voice.radio import PHRASES

    out = [t for t in M.TEXTS.get(lang, {}).values() if "{" not in t]
    out += [t for t in PHRASES.get(lang, {}).values() if t]
    for situations in VARIANTS.get(lang, {}).values():
        for variants in situations.values():
            out += list(variants)
    return list(dict.fromkeys(out))


def _voices_extra(cfg: AppConfig, a: argparse.Namespace, out) -> int | None:
    """``--piper-download`` and ``--prerender``; None when neither was asked for."""
    if getattr(a, "piper_download", None):
        from ..voice.wav_voice import download_piper_voice

        dest = home_dir() / "voices"
        try:
            model = download_piper_voice(a.piper_download, dest)
        except (OSError, ValueError) as exc:
            out(f"could not download {a.piper_download}: {exc}")
            return 1
        out(
            f'downloaded to {model}\nin config/local.yaml:\n  engineer:\n    voice:\n      backend: piper\n      piper_model: "{model}"'
        )
        return 0
    if getattr(a, "prerender", False):
        from ..cli import _with_profile

        cfg = _with_profile(cfg)
        v = cfg.engineer.voice
        try:
            backend = T.create_backend(
                v.backend,
                language=cfg.engineer.language,
                voice_name=v.name,
                rate=v.rate,
                volume=v.volume,
                output_device=v.output_device,
                command=v.command,
                radio_effect=v.radio_effect,
                piper_model=v.piper_model,
                piper_exe=v.piper_exe,
                cache=True,
                cache_dir=v.cache_dir,
                http_url=v.http_url,
                http_body=v.http_body,
                reference_audio=v.reference_audio,
            )
        except T.TtsUnavailable as exc:
            out(f"cannot prepare the voice: {exc}")
            return 1
        if not hasattr(backend, "prerender"):
            out(
                "this voice speaks straight to the output (SAPI without the radio effect): there is nothing to cache. "
                "Set engineer.voice.radio_effect or use the piper backend."
            )
            return 0
        texts = _fixed_phrases(cfg.engineer.language)
        out(f"rendering {len(texts)} fixed phrases...")
        done, had = backend.prerender(texts)
        out(f"{done} rendered, {had} already in the cache ({backend.cache.folder})")
        return 0
    return None


def cmd_voices(cfg: AppConfig, a: argparse.Namespace, *, out: Callable[[str], None] = print) -> int:
    extra = _voices_extra(cfg, a, out)
    if extra is not None:
        return extra
    try:
        voices = T.list_sapi_voices()
    except T.TtsUnavailable as exc:
        out(f"cannot list voices: {exc}")
        return 1
    configured = (cfg.engineer.voice.name or "").lower()
    out("Voices installed in Windows (use a part of the name in engineer.voice.name):")
    for v in voices:
        mark = "*" if configured and configured in v.name.lower() else " "
        out(f" {mark} {v.line()}")
    out(
        "\nTo add more: Windows Settings > Time & language > Speech > Add voices, or any SAPI voice engine; "
        "or set engineer.voice.backend: command with your own synthesiser (see config/default.yaml)."
    )
    if not a.test:
        return 0
    output = a.device or cfg.engineer.voice.output_device
    chosen = [v for v in voices if not a.voice or a.voice.lower() in v.name.lower()]
    out(f"\nPlaying a sample with {len(chosen)} voice(s) on {output or 'the default output'}...")
    for v in chosen:
        tail = SAMPLE.get(v.language, SAMPLE["en"])
        text = tail.format(name=cfg.engineer.persona.name or T.short_name(v.name))
        out(f"  {v.name}")
        tts = T.SapiTts(
            language=cfg.engineer.language,
            voice_name=v.name,
            rate=cfg.engineer.voice.rate,
            volume=cfg.engineer.voice.volume,
            output_device=output,
        )
        try:
            tts.open()
            tts.speak(text)
            deadline = time.monotonic() + 15
            time.sleep(0.2)
            while tts.is_speaking() and time.monotonic() < deadline:
                time.sleep(0.05)
        except T.TtsUnavailable as exc:
            out(f"    could not use it: {exc}")
        finally:
            tts.close()
    return 0


__all__ = ["cmd_devices", "cmd_mic_test", "cmd_voices", "sys"]
