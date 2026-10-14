"""Microphone selection and capture (fake audio), voices and outputs, the external-command voice."""

from __future__ import annotations

import pathlib
import sys
import wave

import numpy as np
import pytest

from pit_wall_ai.audio.capture import (
    SAMPLE_RATE,
    Microphone,
    Utterance,
    UtteranceRecorder,
    _resample,
    read_wav,
    write_wav,
)
from pit_wall_ai.audio.cli_audio import cmd_devices, cmd_mic_test, cmd_voices
from pit_wall_ai.audio.devices import AudioDevice, DeviceNotFound, find_device, usable
from pit_wall_ai.cli import main
from pit_wall_ai.config import AppConfig
from pit_wall_ai.voice import tts as T


def dev(i, name, api, inp=1, out=0, rate=48000.0):
    return AudioDevice(i, name, api, inp, out, rate)


DEVICES = [
    dev(0, "Microsoft Sound Mapper - Input", "MME"),
    dev(1, "Microphone (Razer Seiren Mini)", "MME", rate=44100.0),
    dev(2, "Microphone (2- USB Camera-B4.09", "MME"),  # MME cuts names at 31 characters
    dev(9, "Microphone (Razer Seiren Mini)", "Windows DirectSound", rate=44100.0),
    dev(21, "Microphone (Razer Seiren Mini)", "Windows WASAPI"),
    dev(20, "Microphone (2- USB Camera-B4.09.24.1)", "Windows WASAPI"),
    dev(
        23,
        "Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0",
        "Windows WDM-KS",
        rate=8000.0,
    ),
    dev(30, "Headphones (Logitech G Pro)", "Windows WASAPI", inp=0, out=2),
    dev(31, "Microphone (Logitech G Pro)", "Windows WASAPI", rate=48000.0),
]


# -- device selection ------------------------------------------------------------------------------------------
def test_one_entry_per_physical_device_with_the_preferred_host_api():
    ins = usable(DEVICES, kind="input")
    names = [d.name for d in ins]
    assert "Microsoft Sound Mapper - Input" not in names  # the mapper is not a device
    seiren = [d for d in ins if "Seiren" in d.name]
    assert len(seiren) == 1 and seiren[0].hostapi == "Windows WASAPI"
    cam = [d for d in ins if "USB Camera" in d.name]
    assert (
        len(cam) == 1 and cam[0].hostapi == "Windows WASAPI"
    )  # the 31-character MME name merged with it
    assert not any(d.hostapi == "Windows WDM-KS" for d in ins)  # exclusive mode is left out


def test_a_device_is_found_by_a_part_of_its_name_case_insensitive():
    d, others = find_device(DEVICES, "SEIREN", kind="input")
    assert d.index == 21 and others == []
    d, _ = find_device(DEVICES, "logitech", kind="input")
    assert d.name.startswith("Microphone (Logitech")  # a microphone, not the headphones output
    d, _ = find_device(DEVICES, "logitech", kind="output")
    assert d.name.startswith("Headphones")


def test_no_query_means_the_system_default_and_a_bad_query_lists_what_exists():
    assert find_device(DEVICES, None, kind="input") == (None, [])
    with pytest.raises(DeviceNotFound, match="Seiren"):
        find_device(DEVICES, "does not exist", kind="input")


def test_a_narrowband_bluetooth_device_is_flagged():
    assert dev(1, "Headset", "MME", rate=8000.0).narrowband
    assert not dev(1, "Mic", "MME", rate=48000.0).narrowband


# -- the recorder: pre-roll, tail, accidental touches ---------------------------------------------------------------
def block(value: int, n: int = 320) -> np.ndarray:  # 20 ms at 16 kHz
    return np.full(n, value, dtype=np.int16)


def make(**kw):
    got: list[Utterance] = []
    return UtteranceRecorder(on_utterance=got.append, **kw), got


def test_an_utterance_is_the_preroll_the_held_audio_and_the_tail():
    rec, got = make(preroll_ms=100, tail_ms=60, min_ms=1, silence_rms=0.0)
    for _ in range(10):  # 200 ms of "before": only the last 100 ms are kept
        rec.feed(block(100))
    rec.press()
    for _ in range(5):  # 100 ms held
        rec.feed(block(5000))
    rec.release()
    for _ in range(5):  # the tail, then silence that must not be recorded
        rec.feed(block(200))
    assert len(got) == 1
    pcm = got[0].pcm
    assert (
        abs(len(pcm) - (1600 + 1600 + 960)) <= 320
    )  # 100 ms pre-roll + 100 ms held + 60 ms tail (block size)
    assert pcm[0] == 100 and (pcm == 5000).sum() == 1600
    assert got[0].duration_s == pytest.approx(len(pcm) / SAMPLE_RATE)


def test_a_touch_shorter_than_the_minimum_or_silent_is_dropped():
    rec, got = make(preroll_ms=0, tail_ms=0, min_ms=300, silence_rms=0.002)
    rec.press()
    rec.feed(block(8000))  # 20 ms
    rec.release()
    rec.feed(block(0))
    assert got == [] and rec.dropped == 1
    rec.press()
    for _ in range(25):  # 500 ms of digital silence
        rec.feed(block(0))
    rec.release()
    rec.feed(block(0))
    assert got == [] and rec.dropped == 2


def test_a_stuck_button_does_not_record_for_ever():
    rec, got = make(preroll_ms=0, tail_ms=0, min_ms=1, silence_rms=0.0, max_s=0.5)
    rec.press()
    for _ in range(60):
        rec.feed(block(3000))
    assert len(got) == 1 and got[0].duration_s <= 0.6


def test_nothing_is_kept_while_the_button_is_up():
    rec, got = make(preroll_ms=100, min_ms=1, silence_rms=0.0)
    for _ in range(200):
        rec.feed(block(3000))
    assert (
        got == [] and len(rec._held) == 0 and rec._pre_samples <= 1600 + 320
    )  # only the pre-roll ring


def test_a_second_press_while_recording_is_ignored():
    rec, got = make(preroll_ms=0, tail_ms=0, min_ms=1, silence_rms=0.0)
    rec.press()
    rec.feed(block(4000))
    rec.press()
    rec.feed(block(4000))
    rec.release()
    rec.feed(block(0))
    assert len(got) == 1 and len(got[0].pcm) >= 640


# -- levels and files -------------------------------------------------------------------------------------------------
def test_peak_and_rms_are_fractions_of_full_scale():
    u = Utterance(np.array([0, 16384, -16384, 0], dtype=np.int16))
    assert u.peak == pytest.approx(0.5) and u.rms == pytest.approx(0.3535, abs=1e-3)
    assert Utterance(np.zeros(0, dtype=np.int16)).peak == 0.0


def test_wav_round_trip_and_resampling_of_another_rate(tmp_path):
    t = np.arange(16000) / 16000
    u = Utterance((np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16))
    path = tmp_path / "a.wav"
    write_wav(path, u)
    back = read_wav(path)
    assert back.rate == SAMPLE_RATE and np.array_equal(back.pcm, u.pcm)
    # a 48 kHz stereo file is read as 16 kHz mono
    with wave.open(str(tmp_path / "b.wav"), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(np.repeat(np.zeros(48000, dtype="<i2"), 2).tobytes())
    r = read_wav(tmp_path / "b.wav")
    assert r.rate == SAMPLE_RATE and abs(len(r.pcm) - 16000) <= 2


def test_downsampling_keeps_a_tone_at_its_frequency():
    sr = 48000
    t = np.arange(sr) / sr
    x = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
    y = _resample(x, sr, 16000)
    assert abs(len(y) - 16000) <= 1
    spectrum = np.abs(np.fft.rfft(y.astype(np.float64)))
    assert abs(np.argmax(spectrum) - 440) <= 2


# -- a fake audio backend through the Microphone and the mic-test command ----------------------------------------------
class FakeAudio:
    """Plays a sine into the callback from a thread, like a real input stream would."""

    def __init__(self, devices=DEVICES, fail=False):
        self._devices, self.fail, self.opened = devices, fail, None

    def devices(self):
        return self._devices

    def open_input(self, device, rate, block_n, callback):
        import threading
        import time

        if self.fail:
            raise OSError("exclusive mode")
        self.opened = (device, rate)
        stop = threading.Event()

        def run():
            t = 0
            while not stop.is_set():
                x = (np.sin(2 * np.pi * 300 * (np.arange(block_n) + t) / rate) * 6000).astype(
                    np.int16
                )
                callback(x)
                t += block_n
                time.sleep(block_n / rate)

        th = threading.Thread(target=run, daemon=True)
        th.start()

        class H:
            rate = 16000

            def close(self):
                stop.set()

        return H()


def args(**kw):
    import argparse

    return argparse.Namespace(**{"device": None, "seconds": 0.6, "out": None, **kw})


def test_mic_test_records_reports_levels_and_saves_a_wav(tmp_path):
    lines: list[str] = []
    be = FakeAudio()
    out = tmp_path / "t.wav"
    rc = cmd_mic_test(
        AppConfig(),
        args(device="seiren", out=str(out)),
        backend=be,
        out=lines.append,
        sleep=__import__("time").sleep,
    )
    text = "\n".join(lines)
    assert rc == 0 and be.opened[0].index == 21
    assert (
        "microphone: Microphone (Razer Seiren Mini)" in text
        and "peak" in text
        and "saved to" in text
    )
    assert out.is_file() and read_wav(out).duration_s > 0.3


def test_mic_test_warns_about_a_telephone_quality_device():
    lines: list[str] = []
    dev8 = [dev(5, "Headset", "Windows WASAPI", rate=8000.0)]
    cmd_mic_test(
        AppConfig(),
        args(device="headset"),
        backend=FakeAudio(dev8),
        out=lines.append,
        sleep=__import__("time").sleep,
    )
    assert any("8 kHz" in s for s in lines)


def test_mic_test_says_what_exists_when_the_name_matches_nothing():
    lines: list[str] = []
    rc = cmd_mic_test(AppConfig(), args(device="nothing"), backend=FakeAudio(), out=lines.append)
    assert rc == 2 and "Seiren" in lines[0]


def test_mic_test_reports_a_device_that_cannot_be_opened():
    lines: list[str] = []
    rc = cmd_mic_test(
        AppConfig(), args(device="seiren"), backend=FakeAudio(fail=True), out=lines.append
    )
    assert rc == 1 and any("cannot open" in s for s in lines)


def test_devices_command_lists_microphones_and_marks_the_configured_one(capsys):
    cfg = AppConfig.model_validate({"audio": {"input_device": "seiren"}})
    cmd_devices(cfg, args(), backend=FakeAudio())
    out = capsys.readouterr().out
    assert " * Microphone (Razer Seiren Mini)" in out
    assert "telephone quality" not in out  # the 8 kHz headset is WDM-KS, which is left out


def test_the_microphone_stream_feeds_the_recorder():
    rec, got = make(preroll_ms=0, tail_ms=0, min_ms=50, silence_rms=0.0)
    mic = Microphone(FakeAudio(), None, rec)
    mic.start()
    try:
        import time

        rec.press()
        time.sleep(0.25)
        rec.release()
        time.sleep(0.1)
    finally:
        mic.stop()
    assert len(got) == 1 and got[0].duration_s >= 0.15 and mic.level >= 0.0


# -- voices and outputs ---------------------------------------------------------------------------------------------------
def test_pick_by_name():
    names = [
        "SONY TV (NVIDIA High Definition Audio)",
        "Speakers (Voice.ai Audio Cable)",
        "Headset (G Pro)",
    ]
    assert T.pick_by_name(names, "headset") == "Headset (G Pro)"
    assert T.pick_by_name(names, None) is None
    with pytest.raises(T.TtsUnavailable, match="Headset"):
        T.pick_by_name(names, "nothing")


def test_the_language_is_read_from_the_lcid():
    assert (
        T._lang_of("80A") == "es"
        and T._lang_of("409") == "en"
        and T._lang_of("407").startswith("lcid")
    )


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="SAPI")
def test_installed_voices_and_outputs_are_listed():
    voices = T.list_sapi_voices()
    assert voices and all(v.name for v in voices)
    outs = T.list_sapi_outputs()
    assert isinstance(outs, list)
    lines: list[str] = []
    assert cmd_voices(AppConfig(), args(test=False, voice=None), out=lines.append) == 0
    assert any("Voices installed" in s for s in lines)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="SAPI")
def test_an_unknown_output_device_is_reported_with_the_available_ones():
    tts = T.SapiTts(language="es", output_device="this output does not exist")
    with pytest.raises(T.TtsUnavailable, match="available"):
        tts.open()
    tts.close()


# -- the external-command voice --------------------------------------------------------------------------------------------
def make_wav_script(tmp_path):
    script = tmp_path / "synth.py"
    script.write_text(
        "import sys, wave\n"
        "out, text = sys.argv[1], sys.argv[2]\n"
        "w = wave.open(out, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)\n"
        "w.writeframes(b'\\x00\\x00' * 8000); w.close()\n"
        "open(out + '.txt', 'w', encoding='utf-8').write(text)\n",
        encoding="utf-8",
    )
    return script


@pytest.mark.skipif(sys.platform != "win32", reason="plays through winsound")
def test_the_command_voice_runs_the_synthesiser_and_plays_the_wav(tmp_path, monkeypatch):
    import winsound

    played: list[str] = []
    monkeypatch.setattr(winsound, "PlaySound", lambda f, flags: played.append(f))
    script = make_wav_script(tmp_path)
    tts = T.create_backend(
        "command",
        language="es",
        voice_name=None,
        rate=0,
        volume=100,
        command=f'"{sys.executable}" "{script}" {{out}} {{text}}',
    )
    tts.open()
    tts.speak("Quedan dos vueltas")
    assert len(played) == 1 and played[0].endswith(".wav")
    assert pathlib.Path(played[0] + ".txt").read_text(encoding="utf-8") == "Quedan dos vueltas"
    assert tts.is_speaking()  # 0.5 s of audio: still "speaking" right after
    tts.stop()
    assert not tts.is_speaking()


def test_the_command_voice_needs_an_output_placeholder_and_reports_a_failing_command():
    with pytest.raises(T.TtsUnavailable, match=r"\{out\}"):
        T.CommandTts("synth --text {text}")
    with pytest.raises(T.TtsUnavailable, match="voice.command is empty"):
        T.create_backend("command", language="es", voice_name=None, rate=0, volume=100)
    tts = T.CommandTts("definitely-not-a-program-xyz {out}")
    if sys.platform == "win32":
        with pytest.raises(T.TtsUnavailable, match="failed"):
            tts.speak("hola")


def test_say_command_accepts_a_voice_and_an_output_override(capsys):
    rc = main(["say", "--voice", "console-only-test", "--device", "none", "hola"])
    assert rc in (0, 1)  # the point: the new options are accepted by the parser


def test_a_phone_recording_in_another_format_is_read_as_16k_mono(tmp_path):
    av = pytest.importorskip("av")
    from pit_wall_ai.audio.capture import read_audio

    t = np.arange(16000) / 16000
    wav = tmp_path / "a.wav"
    write_wav(wav, Utterance((np.sin(2 * np.pi * 300 * t) * 8000).astype(np.int16)))
    m4a = tmp_path / "a.m4a"
    inp, out = av.open(str(wav)), av.open(str(m4a), "w")
    st = out.add_stream("aac", rate=16000)
    st.layout = "mono"
    for fr in inp.decode(audio=0):
        fr.pts = None
        for p in st.encode(fr):
            out.mux(p)
    for p in st.encode(None):
        out.mux(p)
    out.close()
    u = read_audio(m4a)
    assert u.rate == SAMPLE_RATE and 0.9 < u.duration_s < 1.3 and u.peak > 0.1
