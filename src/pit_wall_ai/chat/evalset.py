"""The evaluation set of the chat: does it answer right, refuse what it cannot know, and keep numbers where
they belong?

Run on demand against a recorded session (``pit-wall eval --file ...``); with a real key it costs about one
request per model-bound question, so it is not part of the normal test run. The cases never hard-code a
number: an expectation names a *fact* of the session ("the core temperature of the rear right tyre"), and
the runner reads its value from the same fact sheet the model is given. A case whose facts the session does
not have is skipped, and says why.

Categories
* ``local``      the code answers by itself (source must be ``local``);
* ``open``       open questions: the reply must carry the facts that answer it;
* ``impossible`` the context cannot answer: the reply must say it does not have the data, with no number;
* ``trap``       asked with the router OFF, so the model must put the right number on the right wheel / lap:
                 where models fail most.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from . import grounding as G
from .context import build_facts
from .facts import FactSheet, norm
from .session import ChatReply, ChatSession

REFUSALS = (
    "no tengo ese dato",
    "no tengo ese dato",
    "i do not have that data",
    "no puedo confirmar",
    "i cannot confirm",
    "no dispongo de ese dato",
    "todavia no estan disponibles",
)


@dataclass(frozen=True)
class Case:
    id: str
    category: str  # local | open | impossible | trap
    question: str
    mentions: tuple[str, ...] = ()  # fact keys whose value the reply must state
    any_of: tuple[str, ...] = ()  # at least one of these must be stated (a paraphrase is fine)
    refuse: bool = False
    expect_source: str | None = None
    needs: tuple[str, ...] = ()  # fact keys that must exist for the case to make sense
    model_only: bool = False  # ask with the local router off
    note: str = ""


@dataclass
class Result:
    case: Case
    status: str  # pass | fail | skip
    reasons: list[str] = field(default_factory=list)
    reply: ChatReply | None = None
    raw: str = ""
    latency_ms: float = 0.0

    @property
    def violations(self) -> int:
        return len(self.reply.violations) if self.reply else 0


# -- the cases ----------------------------------------------------------------------------------------------
CASES: tuple[Case, ...] = (
    # local: the code answers
    Case("loc-fuel", "local", "¿cuánta gasolina queda?", ("fuel.litres",), expect_source="local"),
    Case(
        "loc-fuel-laps",
        "local",
        "¿cuántas vueltas de gasolina me quedan?",
        ("fuel.laps_left",),
        expect_source="local",
        needs=("fuel.laps_left",),
    ),
    Case(
        "loc-tyres",
        "local",
        "¿cómo están los neumáticos?",
        ("tyres.FL.core_c", "tyres.FR.core_c", "tyres.RL.core_c", "tyres.RR.core_c"),
        expect_source="local",
    ),
    Case(
        "loc-hottest",
        "local",
        "¿cuál neumático está más caliente y a cuántos grados?",
        expect_source="local",
    ),
    Case("loc-brakes", "local", "¿cómo están los frenos?", refuse=False, expect_source="local"),
    Case(
        "loc-last",
        "local",
        "¿cuál fue mi última vuelta?",
        ("laps.last.time_s",),
        expect_source="local",
    ),
    Case(
        "loc-best",
        "local",
        "¿cuál es mi mejor vuelta?",
        ("laps.best.time_s",),
        expect_source="local",
    ),
    Case("loc-session", "local", "¿en qué sesión estoy?", expect_source="local"),
    Case("loc-penalty", "local", "¿tengo alguna penalización?", expect_source="local"),
    Case("loc-alert", "local", "¿qué fue eso?", expect_source="local", needs=("alerts.A0.text",)),
    Case("loc-corner", "local", "¿cuál es la prima esse?", expect_source="local"),
    Case("loc-notyet", "local", "¿qué setup me recomiendas?", refuse=True, expect_source="local"),
    Case("loc-weather", "local", "¿va a llover?", refuse=True, expect_source="local"),
    # open: the model, with the context
    Case(
        "open-summary",
        "open",
        "resúmeme la sesión en dos frases",
        ("laps.best.time_s",),
        note="the reply must not end with a filler 'no tengo ese dato'",
    ),
    Case("open-pace", "open", "¿qué opinas de mi ritmo?", ("laps.last.time_s",)),
    Case(
        "open-consumption",
        "open",
        "¿cuánto consumo por vuelta llevo?",
        ("fuel.per_lap_l",),
        needs=("fuel.per_lap_l",),
        model_only=True,
    ),
    Case(
        "open-enough",
        "open",
        "¿me alcanza la gasolina para tres vueltas más?",
        ("fuel.laps_left",),
        needs=("fuel.laps_left",),
        note="the right answer is no: it must give the laps left",
        model_only=True,
    ),
    Case(
        "open-axle",
        "open",
        "¿hay diferencia de presión entre el eje delantero y el trasero?",
        any_of=("tyres.axles.front_minus_rear_pressure_psi", "tyres.axles.front_pressure_psi"),
        model_only=True,
    ),
    Case(
        "open-alert",
        "open",
        "explícame el último aviso que diste",
        # whatever the alert was (a lap summary or a braking hint) the answer must not invent: it is checked
        # by the grounding like every other case; no specific number is demanded
        needs=("alerts.A0.text",),
    ),
    Case(
        "open-trend",
        "open",
        "¿cómo ha evolucionado mi ritmo vuelta a vuelta?",
        ("laps.recent.L1.time_s",),
        needs=("laps.recent.L1.time_s", "laps.recent.L2.time_s"),
    ),
    # impossible: the context cannot answer
    Case(
        "imp-weather", "impossible", "¿qué temperatura hará mañana?", refuse=True, model_only=True
    ),
    Case(
        "imp-rivals",
        "impossible",
        "¿a qué distancia está el piloto que va primero?",
        refuse=True,
        model_only=True,
    ),
    Case(
        "imp-oil",
        "impossible",
        "¿qué temperatura tiene el aceite del motor?",
        refuse=True,
        model_only=True,
    ),
    Case(
        "imp-laps",
        "impossible",
        "¿cuántas vueltas faltan para terminar la carrera?",
        refuse=True,
        model_only=True,
    ),
    Case(
        "imp-pitlimit",
        "impossible",
        "¿cuál es el límite de velocidad en boxes?",
        refuse=True,
        model_only=True,
    ),
    Case(
        "imp-topspeed",
        "impossible",
        "¿cuál ha sido mi velocidad máxima?",
        refuse=True,
        model_only=True,
    ),
    Case(
        "imp-engine", "impossible", "¿cuánto desgaste tiene el motor?", refuse=True, model_only=True
    ),
    Case(
        "imp-flag", "impossible", "¿hay bandera amarilla en la pista?", refuse=True, model_only=True
    ),
    # trap: the right number on the right wheel / lap (router off)
    Case(
        "trap-rr-temp",
        "trap",
        "¿a cuántos grados está el neumático trasero derecho?",
        ("tyres.RR.core_c",),
        model_only=True,
    ),
    Case("trap-fr-temp", "trap", "¿y el delantero derecho?", ("tyres.FR.core_c",), model_only=True),
    Case(
        "trap-rl-press",
        "trap",
        "¿qué presión tiene el trasero izquierdo?",
        ("tyres.RL.pressure_psi",),
        model_only=True,
    ),
    Case(
        "trap-lap2",
        "trap",
        "¿cuánto tardó la vuelta 2?",
        ("laps.recent.L2.time_s",),
        needs=("laps.recent.L2.time_s",),
        model_only=True,
    ),
    Case(
        "trap-lap1",
        "trap",
        "¿y la vuelta 1?",
        ("laps.recent.L1.time_s",),
        needs=("laps.recent.L1.time_s",),
        model_only=True,
    ),
    Case(
        "trap-delta",
        "trap",
        "¿cuánto más lenta fue la vuelta 2 que la mejor?",
        ("laps.recent.L2.delta_to_best_s",),
        needs=("laps.recent.L2.delta_to_best_s",),
        model_only=True,
    ),
)


# -- judging ------------------------------------------------------------------------------------------------
def _states_fact(reply: str, sheet: FactSheet, key: str) -> bool:
    """The reply states the value of this fact, in any equivalent rendering."""
    f = sheet.by_key(key)
    if f is None:
        return False
    nums = f.accepts()
    if nums:
        return any(any(G._equal(n.value, n.decimals, v) for v in nums) for n in G._numbers(reply))
    return norm(str(f.value)) in norm(reply)


def _refuses(text: str) -> bool:
    t = norm(text)
    return any(r in t for r in REFUSALS)


def judge(case: Case, reply: ChatReply, sheet: FactSheet) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if case.expect_source and reply.source != case.expect_source:
        reasons.append(f"source {reply.source!r}, expected {case.expect_source!r}")
    # a refusal may restate a fact that is true (the grounding check below catches an invented number)
    if case.refuse and not _refuses(reply.text):
        reasons.append("did not say it does not have the data")
    for key in case.mentions:
        if not _states_fact(reply.text, sheet, key):
            reasons.append(f"does not state {key}")
    if case.any_of and not any(_states_fact(reply.text, sheet, k) for k in case.any_of):
        reasons.append("states none of: " + ", ".join(case.any_of))
    if reply.violations:
        reasons.append("grounding: " + "; ".join(f"{v.kind} {v.text!r}" for v in reply.violations))
    if reply.source in ("degraded", "unavailable"):
        reasons.append(f"no model answer ({reply.source}: {reply.note})")
    return ("fail" if reasons else "pass"), reasons


# -- running ------------------------------------------------------------------------------------------------
@dataclass
class EvalReport:
    results: list[Result]
    started: float = 0.0
    duration_s: float = 0.0

    def by_category(self) -> dict[str, list[Result]]:
        out: dict[str, list[Result]] = {}
        for r in self.results:
            out.setdefault(r.case.category, []).append(r)
        return out

    def summary(self) -> dict:
        ran = [r for r in self.results if r.status != "skip"]
        model = [r for r in ran if r.reply is not None and r.reply.source in ("model", "cache")]
        lat = sorted(r.latency_ms for r in model if r.latency_ms)
        return {
            "cases": len(self.results),
            "ran": len(ran),
            "skipped": len(self.results) - len(ran),
            "passed": sum(r.status == "pass" for r in ran),
            "failed": sum(r.status == "fail" for r in ran),
            "model_answers": len(model),
            "answers_with_violations": sum(r.violations > 0 for r in model),
            "latency_median_ms": statistics.median(lat) if lat else None,
            "latency_p95_ms": lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
            if lat
            else None,
        }


def run(
    session: ChatSession,
    cases: Iterable[Case] = CASES,
    *,
    only: set[str] | None = None,
    pace_s: float = 0.0,
    retries: int = 1,
    retry_wait_s: float = 12.0,
    sleep: Callable[[float], None] = time.sleep,
    on_result: Callable[[Result], None] | None = None,
) -> EvalReport:
    report = EvalReport([], started=time.time())
    t0 = time.perf_counter()
    for case in cases:
        if only and case.category not in only:
            continue
        sheet = build_facts(session._inputs())
        missing = [k for k in case.needs if sheet.by_key(k) is None] + [
            k for k in case.mentions if sheet.by_key(k) is None
        ]
        if missing:
            res = Result(case, "skip", [f"the session has no {', '.join(missing)}"])
        else:
            reply = session.ask(case.question, force_model=case.model_only)
            tries = 0
            # an overloaded or resting service says nothing about the model: wait and ask again
            while reply.source == "degraded" and tries < retries:
                tries += 1
                sleep(retry_wait_s)
                reply = session.ask(case.question, force_model=case.model_only)
            status, reasons = judge(case, reply, sheet)
            res = Result(case, status, reasons, reply, reply.raw or reply.text, reply.latency_ms)
            if case.model_only or reply.source in ("model", "cache"):
                sleep(pace_s)  # stay under the per-minute quota
        report.results.append(res)
        if on_result:
            on_result(res)
    report.duration_s = time.perf_counter() - t0
    return report


def to_markdown(report: EvalReport, *, model_note: str = "") -> str:
    s = report.summary()
    lines = ["# Chat evaluation", ""]
    if model_note:
        lines += [model_note, ""]
    lines += [
        f"* cases: {s['cases']} (ran {s['ran']}, skipped {s['skipped']})",
        f"* passed: **{s['passed']} / {s['ran']}**",
        f"* model answers: {s['model_answers']}, with a claim removed by the grounding check: "
        f"{s['answers_with_violations']}",
    ]
    if s["latency_median_ms"] is not None:
        lines.append(
            f"* model latency: median {s['latency_median_ms'] / 1000:.2f} s, "
            f"p95 {s['latency_p95_ms'] / 1000:.2f} s"
        )
    lines.append("")
    for cat, rs in report.by_category().items():
        ran = [r for r in rs if r.status != "skip"]
        ok = sum(r.status == "pass" for r in ran)
        lines += [f"## {cat}: {ok} / {len(ran)}", ""]
        for r in rs:
            mark = {"pass": "PASS", "fail": "FAIL", "skip": "skip"}[r.status]
            tag = r.reply.tag if r.reply else "-"
            lines.append(f"* **{mark}** `{r.case.id}`  {r.case.question}  _[{tag}]_")
            if r.reply:
                lines.append(f"  * reply: {r.reply.text}")
                if r.raw and r.raw != r.reply.text:
                    lines.append(f"  * raw model reply: {r.raw}")
            for why in r.reasons:
                lines.append(f"  * {why}")
        lines.append("")
    return "\n".join(lines)
