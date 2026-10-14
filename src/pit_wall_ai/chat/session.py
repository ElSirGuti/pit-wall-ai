"""The conversation: local answer first, the model for the rest, the grounding check on whatever it says.

Order of what happens to a question:

1. **Local**: the router recognises it and the answer is written from the fact sheet (milliseconds, no quota).
2. **Model**: anything else goes to the configured provider with the compact context; its reply is checked
   against the facts and the sentences that carry a number it cannot back are removed (``source="model"``).
3. **Degraded**: the provider is rate limited, down or refused: the user gets a plain summary built locally
   and the reason (``source="degraded"``). The conversation never ends in a stack trace.
4. **Unavailable**: the cloud model is switched off or has no key: the chat says so clearly, and what it can
   still answer (``source="unavailable"``).
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from decimal import Decimal

from ..config import AppConfig
from ..engineer.client import LLMClient
from ..engineer.llm import LLMRequest, LLMUnavailable, create_provider
from ..engineer.persona import from_config as persona_from_config
from . import grounding as G
from . import local as L
from . import recall as R
from .context import ChatInputs, build_facts
from .facts import FactSheet

log = logging.getLogger(__name__)

SYSTEM = {
    "es": (
        "Eres el ingeniero de carrera de un piloto de simracing y hablas por radio. Responde en español, "
        "en un máximo de dos frases cortas. Usa SOLO los datos del bloque CONTEXT: cada número que escribas "
        "debe estar ahí, con su unidad y atribuido a la rueda, la curva o la vuelta que le corresponde. "
        "Si te piden un dato concreto que no está en CONTEXT responde: No tengo ese dato. Si te piden un "
        "resumen, cuenta con lo que SÍ hay y no añadas esa frase como relleno. No inventes el clima, los "
        "rivales ni nada que no veas. No des consejos de reglaje ni de estrategia de carrera: aún no están "
        "disponibles. Si CONTEXT dice que no hay análisis de frenadas, dilo. Para comparar ejes o lados "
        "usa los valores de tyres.axles. Los tiempos de vuelta se dicen en minutos y segundos "
        "(1:24,913), nunca como 84,913 segundos."
    ),
    "en": (
        "You are the race engineer of a simracing driver and you speak over the radio. Answer in English, in "
        "at most two short sentences. Use ONLY the data in the CONTEXT block: every number you write must be "
        "there, with its unit and attributed to the wheel, corner or lap it belongs to. If the data is not in "
        "CONTEXT answer: I do not have that data. If asked for a summary, use what IS there and do not add "
        "that sentence as filler. Do not invent weather, rivals or anything you "
        "cannot see. Do not give setup or race strategy advice: they are not available yet. If CONTEXT says "
        "there is no braking analysis, say so. To compare axles or sides use the tyres.axles values. "
        "Say lap times as minutes and seconds (1:24.913), never as 84.913 seconds."
    ),
}

_LAP_SECONDS = re.compile(
    r"(?<![\d:.,])(\d{2,3})([.,])(\d{2,3})\s*(?:segundos|seconds|seg|sec|s)\b"
)


def lap_times_as_clock(text: str, lang: str) -> str:
    """ "84.913 segundos" -> "1:24,913": a lap time is read as minutes and seconds, as a race engineer would.

    Only values of a minute or more with two or three decimals (a lap time, not a gap of 0,98 s)."""

    def repl(m: re.Match) -> str:
        whole, frac = m.group(1), m.group(3)
        ms = Decimal(f"{whole}.{frac}")
        if ms < 60:
            return m.group(0)
        minutes, rest = divmod(ms, Decimal(60))
        sep = "," if lang == "es" else "."
        digits = len(frac)
        secs = f"{rest:0{3 + digits}.{digits}f}".replace(".", sep)
        return f"{int(minutes)}:{secs}"

    return _LAP_SECONDS.sub(repl, text)


CAPABILITIES = {
    "es": "gasolina y vueltas restantes, neumáticos, frenos, mejor vuelta y delta, sesión, penalizaciones, "
    "dónde pierdes tiempo y qué curva es cada una",
    "en": "fuel and laps left, tyres, brakes, best lap and delta, session, penalties, where you lose time and "
    "which corner is which",
}


@dataclass(frozen=True)
class ChatReply:
    text: str
    source: str  # local | model | cache | degraded | unavailable
    intents: tuple[str, ...] = ()
    latency_ms: float = 0.0
    model: str = ""
    violations: tuple[G.Violation, ...] = ()
    note: str = ""
    raw: str = ""  # the model's own words before the grounding check removed anything

    @property
    def tag(self) -> str:
        """The short label the terminal prints after the answer."""
        bits = [self.source]
        if self.model and self.source in ("model", "cache"):
            bits.append(self.model)
        if self.latency_ms:
            bits.append(
                f"{self.latency_ms / 1000:.1f} s"
                if self.latency_ms >= 1000
                else f"{self.latency_ms:.0f} ms"
            )
        if self.violations:
            bits.append(f"{len(self.violations)} claim(s) removed")
        if self.note:
            bits.append(self.note)
        return ", ".join(bits)


@dataclass
class ChatStats:
    asked: int = 0
    local: int = 0
    model: int = 0
    cached: int = 0
    degraded: int = 0
    unavailable: int = 0
    claims_removed: int = 0


def make_client(cfg: AppConfig) -> tuple[LLMClient | None, str]:
    """(client, "") or (None, why there is none). Never raises."""
    try:
        provider = create_provider(cfg.llm)
    except LLMUnavailable as exc:
        return None, str(exc)
    return LLMClient(provider, cfg.llm), ""


class ChatSession:
    def __init__(
        self,
        cfg: AppConfig,
        inputs: Callable[[], ChatInputs],
        client: LLMClient | None,
        *,
        unavailable_reason: str = "",
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.cfg = cfg
        self.lang = cfg.engineer.language
        self._inputs = inputs
        self.client = client
        self.unavailable_reason = unavailable_reason
        self._clock = clock
        self.history: deque[tuple[str, str]] = deque(maxlen=max(0, cfg.chat.history_turns))
        self.persona = persona_from_config(cfg.engineer.persona)
        self.stats = ChatStats()

    # -- the public call ----------------------------------------------------------------------------
    def ask(self, question: str, *, force_model: bool = False) -> ChatReply:
        """``force_model`` skips the local router (used by the evaluation to test the model itself)."""
        t0 = self._clock()
        self.stats.asked += 1
        inp = self._inputs()
        sheet = build_facts(inp)
        reply = self._answer(question, inp, sheet, force_model)
        reply = _with_latency(reply, (self._clock() - t0) * 1000.0)
        self.history.append((question, reply.text))
        return reply

    # -- internals ----------------------------------------------------------------------------------
    def _answer(
        self, question: str, inp: ChatInputs, sheet: FactSheet, force_model: bool = False
    ) -> ChatReply:
        if not force_model:
            mem = R.recall(
                question,
                alerts=list(inp.alerts),
                history=list(self.history),
                track=inp.track,
                now=inp.now,
                lang=self.lang,
            )
            if mem is not None:
                self.stats.local += 1
                return ChatReply(mem.text, "local", ("recall",))
        routed = None if force_model else L.route(question, inp.track)
        b = self.cfg.analysis.braking
        if routed is not None:
            ans = L.answer(
                routed,
                sheet,
                lang=self.lang,
                track=inp.track,
                thresholds=(b.min_distance_m, b.min_time_s),
            )
            self.stats.local += 1
            self._self_check(ans.text, sheet, question)
            return ChatReply(ans.text, "local", tuple(i.value for i in ans.intents))
        if self.client is None:
            self.stats.unavailable += 1
            return ChatReply(self._unavailable_text(), "unavailable", note="no model")
        return self._ask_model(question, inp, sheet)

    def _ask_model(self, question: str, inp: ChatInputs, sheet: FactSheet) -> ChatReply:
        assert self.client is not None
        suffix = self.persona.system_suffix(self.lang)
        request = LLMRequest(
            system=SYSTEM[self.lang] + (" " + suffix if suffix else ""),
            user=self._compose(question),
            context=sheet.context(),
        )
        resp = self.client.ask(request, urgent=True)
        if resp is None:
            self.stats.degraded += 1
            why = self.client.last_skip_reason or "unknown"
            return ChatReply(self._degraded_text(sheet, why), "degraded", note=why)
        report = G.check(resp.text, sheet, question)
        text = resp.text
        if not report.ok:
            self.stats.claims_removed += len(report.violations)
            log.warning(
                "claims removed from a model answer",
                extra={"violations": [(v.kind, v.text, v.detail) for v in report.violations]},
            )
            text = G.sanitize(resp.text, report, self.lang)
        age = sheet.value("data_age_s")
        stale = self.cfg.chat.stale_after_s
        text = lap_times_as_clock(text, self.lang)
        if age is not None and age > stale:
            text = (
                f"(datos de hace {age:.0f} s) "
                if self.lang == "es"
                else f"(data from {age:.0f} s ago) "
            ) + text
        if resp.cached:
            self.stats.cached += 1
        else:
            self.stats.model += 1
        return ChatReply(
            text,
            "cache" if resp.cached else "model",
            model=resp.model,
            violations=tuple(report.violations),
            latency_ms=resp.latency_s * 1000.0,
            raw=resp.text,
        )

    def _compose(self, question: str) -> str:
        if not self.history:
            return question
        lines = []
        for q, a in self.history:
            lines.append(
                f"Pregunta anterior: {q}" if self.lang == "es" else f"Previous question: {q}"
            )
            lines.append(
                f"Respuesta anterior: {a}" if self.lang == "es" else f"Previous answer: {a}"
            )
        head = "Conversación reciente:" if self.lang == "es" else "Recent conversation:"
        now = "Pregunta actual:" if self.lang == "es" else "Current question:"
        return "\n".join([head, *lines, now, question])

    def _self_check(self, text: str, sheet: FactSheet, question: str) -> None:
        report = G.check(text, sheet, question)
        if not report.ok:  # a bug in a local builder: loud in the log, never silent
            log.error(
                "a local answer failed its own grounding check",
                extra={"text": text, "violations": [(v.kind, v.text) for v in report.violations]},
            )

    def _unavailable_text(self) -> str:
        cap = CAPABILITIES[self.lang]
        why = self.unavailable_reason
        if self.lang == "es":
            return (
                f"No puedo consultar el modelo en la nube: {why} "
                f"Mientras tanto contesto en local sobre {cap}."
            )
        return f"I cannot reach the cloud model: {why} Meanwhile I answer locally about {cap}."

    def _degraded_text(self, sheet: FactSheet, why: str) -> str:
        parts = []
        for intents in ((L.Intent.SESSION,), (L.Intent.LAPS,), (L.Intent.FUEL,)):
            a = L.answer(L.Routed(intents), sheet, lang=self.lang)
            if a.text and a.text != L.NO_DATA[self.lang]:
                parts.append(a.text)
        head = (
            f"No he podido consultar el modelo ({why}). Esto es lo que sé:"
            if self.lang == "es"
            else f"I could not reach the model ({why}). This is what I know:"
        )
        return " ".join([head, *parts]) if parts else head


def _with_latency(reply: ChatReply, ms: float) -> ChatReply:
    if reply.latency_ms:  # a model call already measured its own
        return reply
    return replace(reply, latency_ms=ms)


__all__ = ["ChatReply", "ChatSession", "ChatStats", "make_client", "field"]
