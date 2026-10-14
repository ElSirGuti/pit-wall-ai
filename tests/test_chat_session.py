"""The conversation end to end: local answers, the model path with grounding, degradation, the CLI."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pit_wall_ai.chat.runtime import ChatRuntime
from pit_wall_ai.chat.session import SYSTEM, ChatSession, make_client
from pit_wall_ai.cli import main
from pit_wall_ai.config import AppConfig, LLMConfig
from pit_wall_ai.engineer.client import LLMClient
from pit_wall_ai.engineer.llm import LLMRateLimited, LLMRequest, LLMResponse

from .test_chat import synthetic_inputs

FIXTURE = Path(__file__).parent / "fixtures" / "live_lfm_magione_race_window.pwrec.gz"


class Script:
    """A provider that plays a list of replies (a string, or an exception to raise) and records requests."""

    name = "script"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[LLMRequest] = []

    def generate(self, request):
        self.requests.append(request)
        item = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(item, BaseException):
            raise item
        return LLMResponse(text=item, model="script-1", latency_s=0.4)


def session(*replies, cfg=None, inputs=None, client=True, reason="", **cfgkw):
    cfg = cfg or AppConfig.model_validate({"chat": cfgkw} if cfgkw else {})
    provider = Script(*replies)
    c = LLMClient(provider, cfg.llm) if client else None
    s = ChatSession(cfg, inputs or (lambda: synthetic_inputs()), c, unavailable_reason=reason)
    return s, provider


# -- local first ----------------------------------------------------------------------------------------
def test_a_known_question_is_answered_locally_without_calling_the_model():
    s, p = session("no deberia llamarse")
    r = s.ask("¿cuánta gasolina queda?")
    assert r.source == "local" and "20,0 litros" in r.text and p.requests == []
    assert s.stats.local == 1 and r.tag.startswith("local")


# -- the model path ----------------------------------------------------------------------------------------
def test_an_open_question_goes_to_the_model_with_the_prompt_and_the_compact_context():
    s, p = session("Vas bien: el neumático delantero derecho está a 98 °C.")
    r = s.ask("¿qué opinas de mi ritmo?")
    assert r.source == "model" and r.model == "script-1" and not r.violations
    req = p.requests[0]
    assert req.system == SYSTEM["es"] and req.user == "¿qué opinas de mi ritmo?"
    assert req.context["tyres"]["FR"]["core_c"] == 98 and req.context["fuel"]["litres"] == 20.0
    assert req.max_output_tokens == AppConfig().llm.max_output_tokens


def test_a_correct_number_on_the_wrong_wheel_is_removed_from_the_model_answer(caplog):
    s, _ = session(
        "El neumático delantero izquierdo está a 98 °C. Las presiones están equilibradas."
    )
    r = s.ask("háblame del estado general del coche")
    assert r.source == "model" and len(r.violations) == 1
    assert r.violations[0].kind == "misattributed"
    assert "98" not in r.text and "presiones están equilibradas" in r.text
    assert s.stats.claims_removed == 1 and "claim(s) removed" in r.tag


def test_an_answer_with_nothing_verifiable_left_is_an_honest_refusal():
    s, _ = session("El neumático delantero izquierdo está a 98 °C.")
    r = s.ask("háblame del estado general del coche")
    assert "No puedo confirmar" in r.text and r.violations


def test_an_invented_number_is_removed_too():
    s, _ = session("Te quedan 3 vueltas de gasolina. Vas bien.")
    r = s.ask("¿tú qué crees de cómo voy?")
    assert [v.kind for v in r.violations] == ["unsupported"] and r.text == "Vas bien."


def test_stale_data_is_announced_in_a_model_answer():
    inp = synthetic_inputs()
    inp.now = inp.snap.t + 12.0
    s, _ = session("Todo en orden.", inputs=lambda: inp)
    r = s.ask("¿cómo lo ves?")
    assert r.text.startswith("(datos de hace 12 s)")


def test_a_repeated_identical_request_is_served_from_the_cache():
    s, p = session("Todo en orden.", history_turns=0)
    first = s.ask("¿cómo lo ves?")
    second = s.ask("¿cómo lo ves?")
    assert first.source == "model" and second.source == "cache" and len(p.requests) == 1
    assert s.stats.cached == 1


def test_history_goes_with_the_next_question_and_is_trimmed():
    s, p = session("Respuesta uno.", "Respuesta dos.", "Respuesta tres.", history_turns=1)
    s.ask("pregunta uno que no es local")
    s.ask("pregunta dos que no es local")
    s.ask("pregunta tres que no es local")
    assert "Conversación reciente" not in p.requests[0].user
    assert "Pregunta anterior: pregunta uno" in p.requests[1].user
    last = p.requests[2].user
    assert "Pregunta anterior: pregunta dos" in last and "pregunta uno" not in last
    assert last.rstrip().endswith("pregunta tres que no es local")


def test_english_prompt_and_wording():
    cfg = AppConfig.model_validate({"engineer": {"language": "en"}})
    s, p = session("Looks fine.", cfg=cfg)
    r = s.ask("what do you think about my pace")
    assert p.requests[0].system == SYSTEM["en"] and r.text == "Looks fine."


# -- degradation -----------------------------------------------------------------------------------------
def test_a_rate_limited_model_degrades_to_a_local_summary_with_the_reason():
    s, _ = session(LLMRateLimited("quota", 30.0))
    r = s.ask("¿qué opinas de mi ritmo?")
    assert r.source == "degraded" and "provider_429" in r.text
    assert "Esto es lo que sé" in r.text and "Sesión" in r.text and "litros" in r.text
    assert s.stats.degraded == 1


def test_without_a_client_the_chat_says_so_and_still_answers_locally():
    reason = "the cloud model is disabled (llm.allow_cloud: false)."
    s, _ = session(client=False, reason=reason)
    r = s.ask("explícame la chicane")
    assert r.source == "unavailable" and "llm.allow_cloud" in r.text
    assert "gasolina" in r.text and "neumáticos" in r.text  # what still works
    assert s.ask("¿cuánta gasolina queda?").source == "local"


def test_make_client_reports_why_there_is_none_for_a_disabled_cloud():
    cfg = AppConfig(llm=LLMConfig(provider="gemini", allow_cloud=False))
    client, why = make_client(cfg)
    assert client is None and "allow_cloud" in why
    assert make_client(AppConfig())[0] is not None  # the offline mock provider


# -- runtime and CLI ----------------------------------------------------------------------------------------
def test_a_replayed_recording_gives_laps_alerts_and_a_track_context():
    rt = ChatRuntime(AppConfig(), live=False)
    n = rt.feed_file(FIXTURE, session="race")
    assert n > 4000
    inp = rt.inputs()
    assert inp.best is not None and len(inp.laps) >= 2 and inp.snap is not None
    assert {a.rule for a in inp.alerts}  # the silent engineer remembered what it would have said
    s = ChatSession(AppConfig(), rt.inputs, None, unavailable_reason="off")
    assert "Última vuelta" in s.ask("¿cuál fue mi última vuelta?").text
    assert "aviso fue" in s.ask("¿qué fue eso?").text


def test_ask_command_on_a_recording(capsys):
    rc = main(["ask", "--file", str(FIXTURE), "¿cuánta gasolina queda?"])
    out = capsys.readouterr()
    assert rc == 0 and "litros" in out.out and "[local" in out.out


def test_chat_command_reads_questions_until_exit(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.stdin", io.StringIO("¿cuánta gasolina queda?\n\nhola qué tal\nsalir\nno\n")
    )
    rc = main(["chat", "--file", str(FIXTURE)])
    out = capsys.readouterr().out
    assert (
        rc == 0 and out.count("[local") == 1 and "[model" in out
    )  # the offline mock answers the open one
    assert "Ingeniero de carrera" in out


def test_chat_command_tells_when_the_cloud_model_is_off(monkeypatch, capsys, tmp_path):
    cfgfile = tmp_path / "c.yaml"
    cfgfile.write_text("llm:\n  provider: gemini\n  allow_cloud: false\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("explícame la chicane\nsalir\n"))
    rc = main(["chat", "--config", str(cfgfile), "--file", str(FIXTURE)])
    out = capsys.readouterr().out
    assert (
        rc == 0 and "cloud model off" in out and "llm.allow_cloud" in out and "[unavailable" in out
    )


@pytest.mark.parametrize("q", ["¿cuánta gasolina queda?", "¿cómo están los neumáticos?"])
def test_local_answers_on_a_real_recording_pass_their_own_grounding(q):
    from pit_wall_ai.chat import grounding as G
    from pit_wall_ai.chat.context import build_facts

    rt = ChatRuntime(AppConfig(), live=False)
    rt.feed_file(FIXTURE, session="race")
    inp = rt.inputs()
    s = ChatSession(AppConfig(), rt.inputs, None)
    text = s.ask(q).text
    assert G.check(text, build_facts(inp), q).ok, text
