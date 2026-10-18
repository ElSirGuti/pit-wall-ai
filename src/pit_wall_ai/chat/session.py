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
from . import smalltalk
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
    r"(?<![\d:.,])(\d{2,3})([.,])(\d{2,3})\s*(?:segundos|seconds|sekunden|seg|sec|sek|s)\b",
    re.IGNORECASE,
)


def decimal_comma(text: str, lang: str) -> str:
    """Spanish and German write 2,0 litros / 2,0 Liter; the model copies the dots of the context."""
    if lang not in ("es", "de"):
        return text
    return re.sub(r"(?<=\d)\.(?=\d)", ",", text)


def lap_times_as_clock(text: str, lang: str) -> str:
    """ "84.913 segundos" -> "1:24,913": a lap time is read as minutes and seconds, as a race engineer would.

    Only values of a minute or more with two or three decimals (a lap time, not a gap of 0,98 s)."""

    def repl(m: re.Match) -> str:
        whole, frac = m.group(1), m.group(3)
        ms = Decimal(f"{whole}.{frac}")
        if ms < 60:
            return m.group(0)
        minutes, rest = divmod(ms, Decimal(60))
        sep = "," if lang in ("es", "de") else "."
        digits = len(frac)
        secs = f"{rest:0{3 + digits}.{digits}f}".replace(".", sep)
        return f"{int(minutes)}:{secs}"

    return _LAP_SECONDS.sub(repl, text)


SYSTEM["de"] = (
    "Du bist der Renningenieur eines Simracing-Fahrers und sprichst über Funk. Antworte auf Deutsch, "
    "in höchstens zwei kurzen Sätzen. Benutze NUR die Daten im Block CONTEXT: jede Zahl, die du schreibst, "
    "muss dort stehen, mit ihrer Einheit und dem Rad, der Kurve oder der Runde zugeordnet, zu der sie gehört. "
    "Wenn nach einem konkreten Wert gefragt wird, der nicht in CONTEXT steht, antworte: Diese Angabe habe "
    "ich nicht. Bei einer Zusammenfassung nutze, was da ist, und füge diesen Satz nicht als Füllsel an. "
    "Erfinde kein Wetter, keine Gegner und nichts, was du nicht siehst. Gib keine Setup- oder "
    "Rennstrategie-Ratschläge: sie sind noch nicht verfügbar. Wenn CONTEXT sagt, dass es keine "
    "Bremsanalyse gibt, sag das. Zum Vergleich von Achsen oder Seiten nutze die Werte in tyres.axles. "
    "Rundenzeiten sagst du in Minuten und Sekunden (1:24,913), nie als 84,913 Sekunden."
)

CAPABILITIES = {
    "es": "gasolina y vueltas restantes, neumáticos, frenos, mejor vuelta y delta, sesión, penalizaciones, "
    "dónde pierdes tiempo y qué curva es cada una",
    "en": "fuel and laps left, tyres, brakes, best lap and delta, session, penalties, where you lose time and "
    "which corner is which",
    "de": "Sprit und verbleibende Runden, Reifen, Bremsen, schnellste Runde und Delta, Session, Strafen, "
    "wo du Zeit verlierst und welche Kurve welche ist",
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


_CHECKLIST_WORDS = (
    "checklist",
    "lista de practica",
    "plan de practica",
    "mi lista",
    "la lista",
    "que me falta",
    "que falta de la",
)
_EDIT_VERBS = (
    "quita",
    "quitar",
    "elimina",
    "borra",
    "anade",
    "agrega",
    "pon",
    "salta",
    "cambia",
    "mueve",
    "sube",
    "baja",
    "simula",
    "anadir",
)


_RULE_TALK = (
    "parada obligatoria",
    "paradas obligatorias",
    "no hay parada",
    "sin parada",
    "ventana de parada",
    "ventana de paradas",
    "solo gasolina",
    "solo de gasolina",
    "dos compuestos",
    "compuestos distintos",
    "mandatory stop",
    "pit window",
)


def _is_checklist_talk(question: str) -> bool:
    q = " ".join(L.norm(question).split())
    if "?" in question and not any(w in q for w in _CHECKLIST_WORDS):
        return False  # a question about the rules is a question, not a statement of them
    return (
        any(w in q for w in _CHECKLIST_WORDS)
        or any(w in q for w in _RULE_TALK)
        or ("deshaz" in q and "lista" in q)
    )


def _spoken_checklist(cl) -> str:
    items = cl.visible()
    if not items:
        return "La lista está vacía."
    done = sum(1 for i in items if i.status == "done")
    nxt = next((i for i in items if i.status in ("pending", "active")), None)
    text = f"Llevas {done} de {len(items)} hechos."
    if nxt is not None:
        text += f" Sigue: {nxt.title()}."
        if nxt.note:
            text += f" Ojo: {nxt.note}."
    return text


_DEBRIEF_ASKS = (
    "resumen de la sesion",
    "resumen de la carrera",
    "resumen de la clasificacion",
    "dame el resumen",
    "como fue la sesion",
    "como estuvo la sesion",
    "debrief",
    "session summary",
    "summary of the session",
    "sum up the session",
    "zusammenfassung der sitzung",
    "fasse die sitzung zusammen",
)


def _asks_for_debrief(question: str) -> bool:
    q = " ".join(L.norm(question).split())
    return any(a in q for a in _DEBRIEF_ASKS)


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
                previous=self._previous(inp),
            )
            if mem is not None:
                self.stats.local += 1
                return ChatReply(mem.text, "local", ("recall",))
        if not force_model and inp.checklist is not None and _is_checklist_talk(question):
            return self._checklist_reply(question, inp)
        if not force_model and inp.debrief is not None and _asks_for_debrief(question):
            self.stats.local += 1
            return ChatReply(inp.debrief(), "local", ("debrief",))
        routed = None if force_model else L.route(question, inp.track, self.lang)
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
        if not force_model and (chat := smalltalk.reply(question, self.lang)) is not None:
            self.stats.local += 1
            return ChatReply(chat, "local", ("smalltalk",))
        if self.client is None:
            self.stats.unavailable += 1
            return ChatReply(self._unavailable_text(), "unavailable", note="no model")
        return self._ask_model(question, inp, sheet)

    def _checklist_reply(self, question: str, inp: ChatInputs) -> ChatReply:
        from ..analysis.checklist_edit import edit_with_model

        cl = inp.checklist
        q = " ".join(L.norm(question).split())
        self.stats.local += 1
        if any(w in q for w in ("deshaz", "deshacer", "undo")):
            res = cl.undo()
            self._save_checklist(inp)
            return ChatReply(
                "Deshecho." if res.ok else "No hay nada que deshacer.", "local", ("checklist",)
            )
        states_a_rule = any(w in q for w in _RULE_TALK)
        if not states_a_rule and not any(v in q.split() for v in _EDIT_VERBS):
            return ChatReply(_spoken_checklist(cl), "local", ("checklist",))
        ok, text = edit_with_model(self.client, cl, question, lang=self.lang)
        if ok:
            self._save_checklist(inp)
        return ChatReply(text, "model" if self.client is not None else "local", ("checklist",))

    def _save_checklist(self, inp: ChatInputs) -> None:
        inp.checklist.save()  # to the file it was loaded from

    @staticmethod
    def _previous(inp: ChatInputs):
        """Alerts of earlier sessions on this track (empty when the memory is off or this is a replay)."""
        mem = inp.memory
        if mem is None:
            return []
        track = inp.track.track if inp.track is not None else None
        try:
            return mem.previous(track)
        except Exception:  # noqa: BLE001 - a damaged memory file must never break the chat
            log.exception("could not read the alert memory")
            return []

    def _ask_model(self, question: str, inp: ChatInputs, sheet: FactSheet) -> ChatReply:
        assert self.client is not None
        suffix = self.persona.system_suffix(self.lang)
        request = LLMRequest(
            system=SYSTEM[self.lang] + (" " + suffix if suffix else ""),
            user=self._anonymous(self._compose(question), sheet),
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
        text = decimal_comma(lap_times_as_clock(text, self.lang), self.lang)
        if age is not None and age > stale:
            stale_note = {
                "es": f"(datos de hace {age:.0f} s) ",
                "en": f"(data from {age:.0f} s ago) ",
                "de": f"(Daten von vor {age:.0f} s) ",
            }[self.lang]
            text = stale_note + text
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

    def _anonymous(self, text: str, sheet: FactSheet) -> str:
        """The text with the names of the other drivers replaced: the history holds earlier local answers."""
        for name in sorted(sheet.private_values(), key=len, reverse=True):
            other = {"es": "otro piloto", "de": "ein anderer Fahrer"}.get(
                self.lang, "another driver"
            )
            text = re.sub(re.escape(name), other, text, flags=re.IGNORECASE)
        return text

    def _compose(self, question: str) -> str:
        if not self.history:
            return question
        labels = {
            "es": {
                "pq": "Pregunta anterior",
                "pa": "Respuesta anterior",
                "head": "Conversación reciente:",
                "now": "Pregunta actual:",
            },
            "en": {
                "pq": "Previous question",
                "pa": "Previous answer",
                "head": "Recent conversation:",
                "now": "Current question:",
            },
            "de": {
                "pq": "Vorherige Frage",
                "pa": "Vorherige Antwort",
                "head": "Letzte Unterhaltung:",
                "now": "Aktuelle Frage:",
            },
        }[self.lang]
        lines = []
        for q, a in self.history:
            lines.append(f"{labels['pq']}: {q}")
            lines.append(f"{labels['pa']}: {a}")
        head, now = labels["head"], labels["now"]
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
        if self.lang == "de":
            return (
                f"Ich kann das Cloud-Modell nicht erreichen: {why} "
                f"Bis dahin antworte ich lokal zu {cap}."
            )
        if self.lang == "es":
            return (
                f"No puedo consultar el modelo en la nube: {why} "
                f"Mientras tanto contesto en local sobre {cap}."
            )
        return f"I cannot reach the cloud model: {why} Meanwhile I answer locally about {cap}."

    def _degraded_text(self, sheet: FactSheet, why: str) -> str:
        log.warning("model unreachable, answering with what is known", extra={"why": why})
        parts = []
        for intents in ((L.Intent.SESSION,), (L.Intent.LAPS,), (L.Intent.FUEL,)):
            if intents == (L.Intent.SESSION,) and sheet.value("session.kind") in (None, "unknown"):
                continue  # "Sesión: unknown, menu" says nothing to a driver
            a = L.answer(L.Routed(intents), sheet, lang=self.lang)
            # a builder with nothing to say answers "no data (...)": that is not something I know
            if a.text and not a.text.startswith(L.NO_DATA[self.lang].rstrip(".")):
                parts.append(a.text)
        # the technical reason goes to the log, not to the driver's ears
        head = {
            "es": "No he podido consultar el modelo ahora mismo."
            + (" Esto es lo que sé:" if parts else ""),
            "en": "I could not reach the model right now."
            + (" This is what I know:" if parts else ""),
            "de": "Ich konnte das Modell gerade nicht erreichen."
            + (" Das weiß ich:" if parts else ""),
        }[self.lang]
        return " ".join([head, *parts]) if parts else head


def _with_latency(reply: ChatReply, ms: float) -> ChatReply:
    if reply.latency_ms:  # a model call already measured its own
        return reply
    return replace(reply, latency_ms=ms)


__all__ = ["ChatReply", "ChatSession", "ChatStats", "make_client", "field"]
