import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from pit_wall_ai.config import AppConfig
from pit_wall_ai.voice.profiles import apply_profile, load_profile, render_http
from pit_wall_ai.voice.tts import TtsUnavailable, create_backend
from pit_wall_ai.voice.wav_voice import WavVoice


def test_a_profile_sets_the_engine_the_effect_the_name_and_the_language(tmp_path):
    (tmp_path / "marta.yaml").write_text(
        'id: marta\ndisplay_name: "Marta"\nengine: piper\nmodel: "C:/v/es.onnx"\nlanguage: es\nradio_effect: light\n',
        encoding="utf-8",
    )
    p = load_profile("marta", tmp_path)
    cfg = apply_profile(AppConfig(), p)
    v = cfg.engineer.voice
    assert v.backend == "piper" and v.piper_model == "C:/v/es.onnx" and v.radio_effect == "light"
    assert cfg.engineer.persona.name == "Marta" and cfg.engineer.language == "es"


def test_a_missing_or_wrong_profile_is_reported_clearly(tmp_path):
    with pytest.raises(TtsUnavailable, match="not found"):
        load_profile("nope", tmp_path)
    (tmp_path / "bad.yaml").write_text("id: bad\nengine: telepathy\n", encoding="utf-8")
    with pytest.raises(TtsUnavailable, match="not valid"):
        load_profile("bad", tmp_path)


class Server:
    """A local synthesis server: it records the request and answers with a WAV."""

    def __init__(self, wav: bytes):
        outer = self
        outer.requests = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers["Content-Length"])
                outer.requests.append(json.loads(self.rfile.read(n)))
                self.send_response(200)
                self.send_header("Content-Length", str(len(wav)))
                self.end_headers()
                self.wfile.write(wav)

            def log_message(self, *a):
                return

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}/tts"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


def make_wav(tmp_path):
    p = tmp_path / "s.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x10" * 1600)
    return p.read_bytes()


def test_the_http_engine_posts_the_sentence_and_the_clip_path_and_writes_the_wav(tmp_path):
    srv = Server(make_wav(tmp_path))
    try:
        out = tmp_path / "o.wav"
        render_http(srv.url, None, 'Frena "ya"', str(out), language="es", reference="C:/mi/voz.wav")
        assert out.read_bytes().startswith(b"RIFF")
        assert srv.requests[0] == {
            "text": 'Frena "ya"',
            "language": "es",
            "speaker_wav": "C:/mi/voz.wav",
        }
    finally:
        srv.close()


def test_the_http_engine_only_talks_to_this_machine(tmp_path):
    with pytest.raises(TtsUnavailable, match="only talks to this machine"):
        render_http("http://example.com/tts", None, "hola", str(tmp_path / "o.wav"))


def test_the_backend_factory_builds_the_http_voice():
    kw = dict(language="es", voice_name=None, rate=0, volume=100)
    assert isinstance(create_backend("http", http_url="http://127.0.0.1:1/tts", **kw), WavVoice)
    with pytest.raises(TtsUnavailable):
        create_backend("http", **kw)
