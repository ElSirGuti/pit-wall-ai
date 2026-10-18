"""Questions the code can answer by itself: no model, no network, no quota, milliseconds.

``route`` turns a question into one or more intents with deterministic keyword rules (Spanish and English,
accents and case ignored); ``answer`` writes the reply from the **same fact sheet** the model would receive,
so a local answer and a model answer can never disagree about a number.

An unmatched question returns no intent and goes to the model. Questions about things the context can never
contain (weather, other drivers) are answered locally with "no tengo ese dato" so they cost nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from ..analysis.corner_text import (
    describe_corner,
    describe_from_names,
    find_corner,
    find_in_names,
)
from ..analysis.trackctx import TrackInfo
from ..engineer import messages as M
from .facts import Fact, FactSheet, norm


class Intent(StrEnum):
    CORNER = "corner"
    LAST_ALERT = "last_alert"
    LOSS = "loss"
    PENALTY = "penalty"
    FUEL = "fuel"
    TYRES = "tyres"
    BRAKES = "brakes"
    LAPS = "laps"
    SESSION = "session"
    UNKNOWN_DATA = "unknown_data"
    NOT_YET = "not_yet"  # setup and race strategy: planned for phase 8
    TIME_LEFT = "time_left"
    TYRE_CORNERS = "tyre_corners"  # where the tyres are worked hardest (an estimate)
    STRAIGHT = "straight"  # what a named straight is and between which corners
    CORNER_NAMES = "corner_names"  # the list of the circuit's named corners
    RIVALS = "rivals"  # who is ahead and behind, the gaps, their times (CSP bridge)
    WEATHER = "weather"  # air and track temperature
    RACE_PLAN = "race_plan"  # where to stop, with the rules the driver stated (the 8a planner)
    FUEL_PLAN = "fuel_plan"  # how much fuel for a race length, can I finish, how much to save (the 8a planner)


# keyword tables: every entry is a normalised phrase searched as whole words
_KEYS: dict[Intent, tuple[str, ...]] = {
    Intent.LAST_ALERT: (
        "que fue eso",
        "por que avisaste",
        "por que dijiste",
        "por que me avisaste",
        "ultimo aviso",
        "que me dijiste",
        "what was that",
        "why did you say",
        "why did you warn",
        "last alert",
    ),
    Intent.LOSS: (
        "pierdo",
        "perdi",
        "perdida",
        "perdiendo",
        "pierdes",
        "perder tiempo",
        "donde pierdo",
        "mis frenadas",
        "mi frenada",
        "frenadas",
        "frenada",
        "losing time",
        "where am i losing",
        "lose time",
        "braking points",
        "braking",
        "donde mejoro",
        "donde puedo mejorar",
    ),
    Intent.PENALTY: ("penalizacion", "penalizaciones", "sancion", "penalty", "penalties"),
    Intent.FUEL: (
        "gasolina",
        "combustible",
        "fuel",
        "litros",
        "consumo",
        "consumption",
        "cuanto me queda de",
    ),
    Intent.TYRES: (
        "mas caliente",
        "mas calientes",
        "mas fria",
        "mas frio",
        "hottest",
        "coldest",
        "neumatico",
        "neumaticos",
        "gomas",
        "llantas",
        "presion",
        "presiones",
        "tyre",
        "tyres",
        "tire",
        "tires",
        "pressure",
        "delanteros",
        "delanteras",
        "traseros",
        "traseras",
        "delantero izquierdo",
        "delantero derecho",
        "trasero izquierdo",
        "trasero derecho",
        "delantera izquierda",
        "delantera derecha",
        "trasera izquierda",
        "trasera derecha",
        "front left",
        "front right",
        "rear left",
        "rear right",
    ),
    Intent.BRAKES: ("freno", "frenos", "brake", "brakes", "brake temperature"),
    Intent.LAPS: (
        "ultima vuelta",
        "vuelta anterior",
        "mejor vuelta",
        "best lap",
        "last lap",
        "delta",
        "diferencia",
        "mis vueltas",
        "tiempos",
        "lap times",
        "como voy de tiempo",
        "mi mejor",
    ),
    Intent.SESSION: (
        "que sesion",
        "en que sesion",
        "tipo de sesion",
        "what session",
        "which session",
        "session type",
        "en que estoy",
        "mi posicion",
        "my position",
        "en que posicion",
        "que posicion",
    ),
    Intent.UNKNOWN_DATA: (
        "clima",
        "meteorologia",
        "meteorologico",
        "va a llover",
        "llovera",
        "lluvia",
        "weather",
        "rain",
        "forecast",
        "rival",
        "rivales",
        "oponente",
        "oponentes",
        "adversario",
        "adversarios",
        "quien va",
        "opponent",
        "opponents",
        "rivals",
    ),
}
_IDENTITY = (
    "cual es",
    "que curva",
    "cual es esa",
    "cual es la",
    "cual es el",
    "donde esta",
    "which corner",
    "which one",
    "what corner",
    "where is",
    "which is",
    "que es",
    "cual",
)
# a question that asks what a straight is or where it is (not "do I lose time on it")
_STRAIGHT_Q = (
    "que es",
    "cual es",
    "cual",
    "donde esta",
    "donde queda",
    "donde",
    "what is",
    "what s",
    "where is",
    "which is",
    "was ist",
    "wo ist",
    "wo liegt",
    "welche",
)
_KEYS[Intent.NOT_YET] = (
    "setup",
    "reglaje",
    "reglajes",
    "configuracion del coche",
    "estrategia",
    "cuando debo parar",
    "cuando parar",
    "cuando paro",
    "tyre strategy",
    "estrategia de neumaticos",
)
_KEYS[Intent.RACE_PLAN] = (
    "plan de carrera",
    "estrategia de carrera",
    "estrategia",
    "strategy",
    "cuando debo parar",
    "cuando parar",
    "cuando paro",
    "cuantas paradas",
    "when should i pit",
    "when to pit",
    "pit strategy",
    "race plan",
    "how many stops",
)
_PIT_DECISION = (
    "deberia entrar",
    "debo entrar",
    "entrar a boxes",
    "entrar a pits",
    "entrar a pit",
    "entrar a los boxes",
    "entro a boxes",
    "entro a pits",
    "parar en boxes",
    "parar a boxes",
    "cambiar los neumaticos",
    "cambio de neumaticos",
    "should i pit",
    "should i come in",
    "should i box",
    "pit now",
    "box now",
)
_KEYS[Intent.NOT_YET] = (*_KEYS[Intent.NOT_YET], *_PIT_DECISION)
_RIVAL_AHEAD = (
    "el de adelante",
    "quien tengo adelante",
    "quien va adelante",
    "quien esta adelante",
    "rival de adelante",
    "coche de adelante",
    "piloto de adelante",
    "piloto de delante",
    "piloto delante",
    "piloto adelante",
    "el piloto de adelante",
    "el de delante",
    "quien tengo delante",
    "quien va delante",
    "quien esta delante",
    "rival de delante",
    "coche de delante",
    "car ahead",
    "who is ahead",
    "who s ahead",
    "driver ahead",
    "wer ist vor mir",
    "der vor mir",
)
_RIVAL_BEHIND = (
    "el de atras",
    "piloto de atras",
    "piloto detras",
    "piloto atras",
    "el piloto de atras",
    "el que va detras",
    "el que va atras",
    "el de detras",
    "quien tengo detras",
    "quien tengo atras",
    "quien va detras",
    "quien viene detras",
    "rival de atras",
    "coche de atras",
    "car behind",
    "who is behind",
    "who s behind",
    "driver behind",
    "wer ist hinter mir",
    "der hinter mir",
)
_RIVAL_LEADER = (
    "el lider",
    "quien es el lider",
    "quien lidera",
    "quien va primero",
    "quien va de primero",
    "quien va en primer lugar",
    "quien va ganando",
    "the leader",
    "who leads",
    "who is leading",
    "who is winning",
    "wer fuhrt",
    "der fuhrende",
)
_SECTOR_WORDS = (
    "sector",
    "sectores",
    "sectors",
    "sektor",
    "sektoren",
    "mas lento",
    "mas rapido",
    "donde pierde",
    "donde gana",
    "donde es mas",
)
_KEYS[Intent.FUEL_PLAN] = (
    "cuanta gasolina debo poner",
    "cuanta gasolina tengo que poner",
    "cuanta gasolina pongo",
    "cuanta gasolina cargo",
    "cuanta gasolina cargar",
    "cuanta gasolina deberia",
    "cuanta gasolina debo llevar",
    "cuanta gasolina llevo que",
    "cuanto combustible deberia",
    "cuanta gasolina para",
    "cuanto combustible debo",
    "cuanto combustible pongo",
    "cuanto combustible para",
    "gasolina para la carrera",
    "gasolina para la clasificacion",
    "gasolina para llegar",
    "llego al final",
    "llegare al final",
    "voy a llegar al final",
    "me alcanza la gasolina",
    "me alcanza el combustible",
    "cuanto tengo que ahorrar",
    "tengo que ahorrar",
    "cuanto debo ahorrar",
    "how much fuel should",
    "how much fuel do i need",
    "how much fuel to put",
    "fuel for the race",
    "can i make it to the end",
    "will i make it to the end",
    "how much do i need to save",
    "wie viel sprit brauche",
    "reicht der sprit",
)
_KEYS[Intent.WEATHER] = (
    "temperatura de pista",
    "temperatura de la pista",
    "temperatura del asfalto",
    "temperatura del aire",
    "temperatura ambiente",
    "temperatura exterior",
    "que temperatura hace",
    "air temperature",
    "track temperature",
    "road temperature",
    "ambient temperature",
    "strecken temperatur",
    "streckentemperatur",
    "lufttemperatur",
    "asphalttemperatur",
)
_KEYS[Intent.RIVALS] = (
    *_RIVAL_AHEAD,
    *_RIVAL_BEHIND,
    *_RIVAL_LEADER,
    "mis rivales",
    "mi rival",
    "my rivals",
    "my rival",
    "a cuanto esta el",
    "a cuanto estan",
    "intervalo",
    "gap to",
    "wie weit",
)
_KEYS[Intent.CORNER_NAMES] = (
    "nombres de las curvas",
    "como se llaman las curvas",
    "como se llaman las curvas de este circuito",
    "que curvas hay",
    "lista de curvas",
    "curvas con nombre",
    "nombres de curvas",
    "names of the corners",
    "corner names",
    "list the corners",
    "name the corners",
    "what are the corners called",
)
_KEYS[Intent.TYRE_CORNERS] = (
    "en que curvas gasto",
    "en que curvas estoy gastando",
    "que curvas gasto",
    "que curvas desgastan",
    "que curvas gastan",
    "donde gasto mas los neumaticos",
    "donde gasto mas neumaticos",
    "donde estoy gastando los neumaticos",
    "donde desgasto",
    "curvas que mas desgastan",
    "gastando mas los neumaticos",
    "which corners wear",
    "where am i wearing",
    "where do i wear",
    "wearing the tyres most",
    "wearing the tyres the most",
    "which corners are wearing",
)
_KEYS[Intent.TIME_LEFT] = (
    "cuanto tiempo queda",
    "cuanto falta",
    "cuanto queda de sesion",
    "cuanto queda de carrera",
    "cuanto dura",
    "cuanto dura la carrera",
    "cuanto dura la sesion",
    "time left",
    "how long is the race",
    "how much time",
    "how long is left",
    "when does it end",
)
_FORCE_MODEL = (
    "resumeme",
    "resumen",
    "resume",
    "resumir",
    "summary",
    "summarize",
    "summarise",
    "explicame",
    "explica",
    "explain",
    "opinas",
    "opinion",
    "que tal he conducido",
    "how did i drive",
)
_HOTTEST = ("mas caliente", "mas calientes", "hottest")
_COLDEST = ("mas fria", "mas frio", "coldest")
_ORDER = (
    Intent.CORNER,
    Intent.TYRE_CORNERS,
    Intent.LAST_ALERT,
    Intent.LOSS,
    Intent.PENALTY,
    Intent.FUEL,
    Intent.TYRES,
    Intent.BRAKES,
    Intent.LAPS,
    Intent.SESSION,
    Intent.TIME_LEFT,
    Intent.RACE_PLAN,
    Intent.FUEL_PLAN,
    Intent.WEATHER,
    Intent.RIVALS,
    Intent.CORNER_NAMES,
    Intent.NOT_YET,
    Intent.UNKNOWN_DATA,
)


_FRONT = (
    "delantero",
    "delantera",
    "delanteros",
    "delanteras",
    "front",
    "vorne",
    "vordere",
    "vorderen",
)
_REAR = (
    "trasero",
    "trasera",
    "traseros",
    "traseras",
    "rear",
    "back",
    "hinten",
    "hintere",
    "hinteren",
)
_LEFT = ("izquierdo", "izquierda", "izquierdos", "left", "links", "linke", "linken")
_RIGHT = ("derecho", "derecha", "derechos", "right", "rechts", "rechte", "rechten")
_METRICS = {
    "pressure_psi": ("presion", "presiones", "psi", "pressure", "druck"),
    "core_c": (
        "temperatura",
        "temperaturas",
        "temperature",
        "temperatur",
        "grados",
    ),
    "wear_pct": ("desgaste", "desgastado", "gastado", "wear", "worn", "verschleiss"),
}


def asked_wheels(q: str) -> tuple[str, ...]:
    """The wheels a tyre question is about: one tyre, an axle or a side; empty = all of them."""
    words = set(q.split())
    stems = {w[:6] for w in words}  # vorderreifen, hinterreifen
    front = bool(words & set(_FRONT)) or "vorder" in stems
    rear = bool(words & set(_REAR)) or "hinter" in stems
    left, right = bool(words & set(_LEFT)), bool(words & set(_RIGHT))
    if front == rear and not (left ^ right):
        return ()
    if front and rear:
        return ()
    if front or rear:
        axle = ("FL", "FR") if front else ("RL", "RR")
        if left ^ right:
            side = 0 if left else 1
            return (axle[side],)
        return axle
    side = ("FL", "RL") if left else ("FR", "RR")
    return side


def asked_metric(q: str) -> str | None:
    """The single measurement a tyre question names (pressure, temperature or wear), or ``None``."""
    words = set(q.split())
    hit = [m for m, ws in _METRICS.items() if words & set(ws)]
    return hit[0] if len(hit) == 1 else None


def _wheel_flags(q: str) -> tuple[str, ...]:
    out = []
    wheels = asked_wheels(q)
    if wheels:
        out.append("wheels:" + ",".join(wheels))
    metric = asked_metric(q)
    if metric:
        out.append("metric:" + metric)
    return tuple(out)


def tyre_filter(routed) -> tuple[tuple[str, ...], str | None]:
    wheels: tuple[str, ...] = ()
    metric = None
    for f in routed.flags:
        if f.startswith("wheels:"):
            wheels = tuple(f.split(":", 1)[1].split(","))
        elif f.startswith("metric:"):
            metric = f.split(":", 1)[1]
    return wheels, metric


_LAPS_RE = re.compile(r"(\d+)\s*(?:vueltas?|laps?|runden?)\b")
_MIN_RE = re.compile(r"(\d+)\s*(?:minutos?|mins?|minutes?|minuten)\b")


def _length_flags(q: str) -> tuple[str, ...]:
    """The race length named in a question: ``laps:8`` or ``minutes:10``."""
    m = _LAPS_RE.search(q)
    if m:
        return (f"laps:{m.group(1)}",)
    m = _MIN_RE.search(q)
    return (f"minutes:{m.group(1)}",) if m else ()


def _has(text: str, phrases: tuple[str, ...]) -> bool:
    padded = f" {text} "
    return any(f" {p} " in padded for p in phrases)


@dataclass(frozen=True)
class Routed:
    intents: tuple[Intent, ...]
    corners: tuple[int, ...] = ()
    flags: tuple[str, ...] = ()


def route(question: str, track: TrackInfo | None = None, lang: str = "es") -> Routed | None:
    """The intents of a question, or ``None`` when it should go to the model.

    Spanish and English share one keyword table (a question in either is understood in both); German has
    its own (``local_de``) and is used when ``lang`` is ``"de"``."""
    de = lang == "de"
    if de:
        from . import local_de as DE

        question = DE.fold(question)
    q = norm(question)
    if not q:
        return None
    if _has(q, DE.FORCE_MODEL if de else _FORCE_MODEL):
        return None  # an open question: the model, with the context
    identity = DE.IDENTITY if de else _IDENTITY
    found: list[Intent] = []
    corners: tuple[int, ...] = ()
    hits: list[int] = []
    if track is not None:
        from ..analysis.circuit_names import find_straight

        st = find_straight(track.track, question, track.geometry)
        if st is not None and _has(q, _STRAIGHT_Q):
            return Routed((Intent.STRAIGHT,), (), (f"straight:{st.role}:{st.name}",))
    if track is not None and track.geometry is not None:
        hits = find_corner(track.geometry, question, track.names)
        if hits and _has(q, identity):
            corners = tuple(hits)
            found.append(Intent.CORNER)
    elif (
        track is not None and track.names
    ):  # no racing line here, but the circuit's table names the corners
        hits = find_in_names(question, track.names)
        if hits and _has(q, identity):
            corners = tuple(hits)
            found.append(Intent.CORNER)
    for intent in _ORDER[1:]:
        if _has(q, DE.KEYS.get(intent.value, ()) if de else _KEYS[intent]):
            found.append(intent)
    if not found:
        return None
    # "how was the braking of corner 4": the loss analysis, but about THAT corner, not the worst one
    if Intent.LOSS in found and hits and Intent.CORNER not in found:
        corners = tuple(hits)
    # data the context can never hold wins only when nothing answerable was asked
    if Intent.UNKNOWN_DATA in found and len(found) > 1:
        found.remove(Intent.UNKNOWN_DATA)
    if Intent.RACE_PLAN in found:
        return Routed((Intent.RACE_PLAN,), corners, _length_flags(q))
    # setup / strategy is a question on its own: a clear "not yet" instead of a half answer
    if Intent.NOT_YET in found:
        return Routed(
            (Intent.NOT_YET,), corners, ("pit_decision",) if _has(q, _PIT_DECISION) else ()
        )
    # a corner or an alert explanation is a question on its own
    for solo in (
        Intent.RIVALS,
        Intent.WEATHER,
        Intent.FUEL_PLAN,
        Intent.CORNER_NAMES,
        Intent.CORNER,
        Intent.TYRE_CORNERS,
        Intent.LAST_ALERT,
    ):
        if solo in found:
            if solo is Intent.FUEL_PLAN:
                return Routed((solo,), corners, _length_flags(q))
            if solo is Intent.RIVALS:
                rival_flags = tuple(
                    f
                    for f, words in (
                        ("ahead", _RIVAL_AHEAD),
                        ("behind", _RIVAL_BEHIND),
                        ("leader", _RIVAL_LEADER),
                        ("sectors", _SECTOR_WORDS),
                    )
                    if _has(q, words)
                )
                return Routed((solo,), corners, rival_flags)
            return Routed((solo,), corners)
    # a braking/losing question already covers "frenadas": do not also dump the brake temperatures
    if Intent.LOSS in found and Intent.BRAKES in found:
        found.remove(Intent.BRAKES)
    hot, cold = (DE.HOTTEST, DE.COLDEST) if de else (_HOTTEST, _COLDEST)
    if Intent.FUEL_PLAN in found:
        return Routed((Intent.FUEL_PLAN,), corners, _length_flags(q))
    flags = tuple(
        f
        for f, words in (
            ("hottest", hot),
            ("coldest", cold),
            ("pit_decision", _PIT_DECISION),
            ("ahead", _RIVAL_AHEAD),
            ("behind", _RIVAL_BEHIND),
            ("leader", _RIVAL_LEADER),
            ("sectors", _SECTOR_WORDS),
        )
        if _has(q, words)
    )
    if Intent.TYRES in found and not {"hottest", "coldest"} & set(flags):
        flags += _wheel_flags(q)  # "the hottest tyre" already names which and how
    return Routed(tuple(found), corners, flags)


@dataclass(frozen=True)
class LocalAnswer:
    text: str
    intents: tuple[Intent, ...]
    keys: tuple[str, ...] = ()
    sentences: tuple[str, ...] = field(default=())


# ── number formatting ───────────────────────────────────────────────────────────────────────────────────
def _num(v: float, nd: int, lang: str) -> str:
    s = f"{v:.{nd}f}"
    return s.replace(".", ",") if lang == "es" else s


def _clock(seconds: float, lang: str) -> str:
    m, s = divmod(seconds, 60)
    t = f"{int(m)}:{s:06.3f}"
    return t.replace(".", ",") if lang == "es" else t


_KIND = {
    "es": {
        "practice": "práctica",
        "qualify": "clasificación",
        "race": "carrera",
        "hotlap": "hotlap",
    },
    "en": {"practice": "practice", "qualify": "qualifying", "race": "race", "hotlap": "hotlap"},
}
_WHEEL = {
    "es": {
        "FL": "delantero izquierdo",
        "FR": "delantero derecho",
        "RL": "trasero izquierdo",
        "RR": "trasero derecho",
    },
    "en": {"FL": "front left", "FR": "front right", "RL": "rear left", "RR": "rear right"},
}
NO_DATA = {
    "es": "No tengo ese dato.",
    "en": "I do not have that data.",
    "de": "Diese Angabe habe ich nicht.",
}


def _corner_ref(sheet: FactSheet, number: int, names: dict[int, str], lang: str) -> str:
    name = names.get(number)
    shown = number if sheet.official is None else sheet.official.get(number)
    if shown is None:  # this apex has no number on the circuit's map: only its name, if it has one
        return name or ("una curva sin número" if lang == "es" else "a corner without a number")
    base = f"la curva {shown}" if lang == "es" else f"corner {shown}"
    return f"{name} ({base})" if name and not re.fullmatch(r"(?i)curva\s+\d+", name) else base


def answer(
    routed: Routed,
    sheet: FactSheet,
    *,
    lang: str = "es",
    track: TrackInfo | None = None,
    thresholds: tuple[float, float] = (10.0, 0.1),
) -> LocalAnswer:
    """The text for the routed intents, built only from ``sheet`` (and the geometry for corner questions)."""
    parts: list[str] = []
    keys: list[str] = []
    if lang == "de":
        from . import local_de as DE

        for intent in routed.intents:
            text, used = DE.BUILDERS[intent.value](sheet, track, routed, thresholds)
            if text:
                parts.append(text)
                keys += used
        if not parts:
            parts.append(DE.NO_DATA)
        return LocalAnswer(" ".join(parts), routed.intents, tuple(keys), tuple(parts))
    for intent in routed.intents:
        fn = _BUILDERS[intent]
        text, used = fn(sheet, lang, track, routed, thresholds)
        if text:
            parts.append(text)
            keys += used
    if not parts:
        parts.append(NO_DATA[lang])
    return LocalAnswer(" ".join(parts), routed.intents, tuple(keys), tuple(parts))


# ── one builder per intent: (sheet, lang, track, routed, thresholds) -> (text, fact keys used) ──────────────
def _fuel(sheet, lang, track, routed, th):
    litres = sheet.value("fuel.litres")
    if litres is None:
        return NO_DATA[lang] + (" (gasolina)" if lang == "es" else " (fuel)"), []
    per, left = sheet.value("fuel.per_lap_l"), sheet.value("fuel.laps_left")
    if per is None or left is None:
        if lang == "es":
            return (
                f"Quedan {_num(litres, 1, lang)} litros; todavía no tengo el consumo por vuelta "
                "(hacen falta vueltas cronometradas completas)."
            ), ["fuel.litres"]
        return (
            f"{_num(litres, 1, lang)} litres left; no per-lap consumption yet "
            "(timed full laps are needed)."
        ), ["fuel.litres"]
    phrase = M.laps_phrase(lang, float(left))
    if lang == "es":
        return (
            f"Quedan {_num(litres, 1, lang)} litros; consumes {_num(per, 2, lang)} litros por vuelta, "
            f"es decir {phrase} ({_num(left, 1, lang)} vueltas)."
        ), ["fuel.litres", "fuel.per_lap_l", "fuel.laps_left"]
    return (
        f"{_num(litres, 1, lang)} litres left; {_num(per, 2, lang)} litres per lap, "
        f"which is {phrase} ({_num(left, 1, lang)} laps)."
    ), ["fuel.litres", "fuel.per_lap_l", "fuel.laps_left"]


def _laps(sheet, lang, track, routed, th):
    keys: list[str] = []
    out: list[str] = []
    best, bl = sheet.value("laps.best.time_s"), sheet.value("laps.best.lap")
    last, ll = sheet.value("laps.last.time_s"), sheet.value("laps.last.lap")
    if last is None and best is None:
        return (
            "Todavía no hay vueltas cronometradas." if lang == "es" else "No timed laps yet."
        ), []
    if last is not None:
        keys += ["laps.last.time_s", "laps.last.lap"]
        flags = sheet.value("laps.last.flags")
        extra = f" ({flags})" if flags else ""
        out.append(
            f"Última vuelta, la {ll}: {_clock(last, lang)}{extra}."
            if lang == "es"
            else f"Last lap, number {ll}: {_clock(last, lang)}{extra}."
        )
    if best is not None:
        keys += ["laps.best.time_s", "laps.best.lap"]
        out.append(
            f"Mejor vuelta, la {bl}: {_clock(best, lang)}."
            if lang == "es"
            else f"Best lap, number {bl}: {_clock(best, lang)}."
        )
    if ll is not None and ll == bl and last is not None:
        out.append(
            "La última vuelta es tu mejor vuelta."
            if lang == "es"
            else "The last lap is your best lap."
        )
    d = sheet.value("laps.last.delta_to_best_s")
    if d is not None:
        keys.append("laps.last.delta_to_best_s")
        word = (
            ("más lenta" if d > 0 else "más rápida")
            if lang == "es"
            else ("slower" if d > 0 else "faster")
        )
        out.append(
            f"La última fue {_num(abs(d), 3, lang)} s {word} que la mejor."
            if lang == "es"
            else f"The last one was {_num(abs(d), 3, lang)} s {word} than the best."
        )
    live = sheet.value("laps.current.live_delta_s")
    if live is not None:
        keys.append("laps.current.live_delta_s")
        out.append(
            f"Delta en vivo: {f'{live:+.2f}'.replace('.', ',')} s contra la referencia."
            if lang == "es"
            else f"Live delta: {live:+.2f} s against the reference."
        )
    return " ".join(out), keys


def _tyres(sheet, lang, track, routed, th):
    rows: list[str] = []
    keys: list[str] = []
    asked, metric = tyre_filter(routed)
    for w in asked or ("FL", "FR", "RL", "RR"):
        core, p, wear = (
            sheet.value(f"tyres.{w}.{k}") for k in ("core_c", "pressure_psi", "wear_pct")
        )
        if metric:  # "the pressure of the rear tyres": only that
            core = core if metric == "core_c" else None
            p = p if metric == "pressure_psi" else None
            wear = wear if metric == "wear_pct" else None
            if core is None and p is None and wear is None:
                continue
        elif core is None and p is None:
            continue
        bits = []
        if core is not None:
            bits.append(f"{_num(core, 0, lang)} °C")
            keys.append(f"tyres.{w}.core_c")
        if p is not None:
            bits.append(f"{_num(p, 1, lang)} psi")
            keys.append(f"tyres.{w}.pressure_psi")
        if wear is not None:
            bits.append(
                f"desgaste {_num(wear, 1, lang)} %"
                if lang == "es"
                else f"wear {_num(wear, 1, lang)} %"
            )
            keys.append(f"tyres.{w}.wear_pct")
        rows.append(f"{_WHEEL[lang][w]}: {', '.join(bits)}")
    if not rows:
        return NO_DATA[lang], []
    temps = {w: sheet.value(f"tyres.{w}.core_c") for w in ("FL", "FR", "RL", "RR")}
    temps = {w: t for w, t in temps.items() if t is not None}
    extra = []
    if temps and "hottest" in routed.flags:
        w = max(temps, key=temps.get)
        extra.append(
            f"El más caliente es el {_WHEEL[lang][w]}, a {_num(temps[w], 0, lang)} °C."
            if lang == "es"
            else f"The hottest is the {_WHEEL[lang][w]} one, at {_num(temps[w], 0, lang)} °C."
        )
    if temps and "coldest" in routed.flags:
        w = min(temps, key=temps.get)
        extra.append(
            f"El más frío es el {_WHEEL[lang][w]}, a {_num(temps[w], 0, lang)} °C."
            if lang == "es"
            else f"The coldest is the {_WHEEL[lang][w]} one, at {_num(temps[w], 0, lang)} °C."
        )
    comp = sheet.value("session.compound")
    head = (
        (f"Neumáticos ({comp}): " if lang == "es" else f"Tyres ({comp}): ")
        if comp
        else ("Neumáticos: " if lang == "es" else "Tyres: ")
    )
    return " ".join([*extra, head + "; ".join(rows) + "."]), keys


def _brakes(sheet, lang, track, routed, th):
    rows, keys = [], []
    for w in ("FL", "FR", "RL", "RR"):
        t = sheet.value(f"brakes.{w}.temp_c")
        if t is not None:
            rows.append(f"{_WHEEL[lang][w]}: {_num(t, 0, lang)} °C")
            keys.append(f"brakes.{w}.temp_c")
    if rows:
        return ("Frenos: " if lang == "es" else "Brakes: ") + "; ".join(rows) + ".", keys
    return (
        "La temperatura de frenos no es fiable en este coche: el sensor no se mueve, así que no la uso."
        if lang == "es"
        else "Brake temperature is not reliable on this car: the sensor does not move, so I do not use it."
    ), ["brakes.note"]


def _session(sheet, lang, track, routed, th):
    kind = sheet.value("session.kind")
    if kind is None:
        return NO_DATA[lang], []
    keys = ["session.kind"]
    bits = [_KIND[lang].get(kind, kind)]
    phase = sheet.value("session.phase")
    phase = (
        {"stopped": "parado", "pits": "en boxes", "out_lap": "vuelta de salida"}.get(phase, phase)
        if lang == "es" and phase
        else phase
    )
    if phase and phase not in ("flying",):
        bits.append(phase)
    out = f"Sesión: {', '.join(bits)}." if lang == "es" else f"Session: {', '.join(bits)}."
    left = sheet.value("session.time_left_s")
    if left is not None and left > 0:
        keys.append("session.time_left_s")
        mm, ss = divmod(int(left), 60)
        out += f" Quedan {mm}:{ss:02d}." if lang == "es" else f" {mm}:{ss:02d} left."
    pos = sheet.value("session.position")
    if pos is not None:
        keys.append("session.position")
        out += f" Vas {pos}.º." if lang == "es" else f" Position {pos}."
    laps = sheet.value("session.laps_completed")
    if laps is not None:
        keys.append("session.laps_completed")
        out += f" Vueltas completadas: {laps}." if lang == "es" else f" Laps completed: {laps}."
    return out, keys


def _penalty(sheet, lang, track, routed, th):
    t = sheet.value("penalties.time_s")
    if t is None:
        return ("No tengo penalizaciones activas." if lang == "es" else "No active penalties."), []
    if lang == "es":
        return (f"Tienes una penalización: te quedan {_num(t, 1, lang)} s por cumplir."), [
            "penalties.time_s"
        ]
    return (f"You have a penalty: {_num(t, 1, lang)} s left to serve."), ["penalties.time_s"]


_WHY_ES = (
    (
        "no Assetto Corsa tracks folder",
        "no encuentro la carpeta de pistas de Assetto Corsa en este equipo (analysis.ac_dirs)",
    ),
    (
        "no fast_lane.ai for track",
        "no hay línea de carrera de esta pista en la instalación del juego",
    ),
    (
        "no valid reference lap",
        "todavía no hay una vuelta de referencia válida con el mismo compuesto",
    ),
    ("no line of", "ninguna línea de carrera coincide con la longitud de la pista (¿otro layout?)"),
    ("no game length", "no sé qué trazado de la pista es sin la longitud que da el juego"),
)


def _why(reason: str, lang: str) -> str:
    if lang != "es":
        return reason
    for prefix, text in _WHY_ES:
        if reason.startswith(prefix) or prefix in reason[:60]:
            return text
    return reason


def _loss(sheet, lang, track, routed, th):
    unavailable = sheet.value("analysis.unavailable")
    lap = sheet.value("analysis.lap")
    names = track.names if track is not None else {}
    if lap is None:
        why = unavailable or (
            "no hay análisis de frenadas para esta pista"
            if lang == "es"
            else "no braking analysis for this track"
        )
        why = _why(str(why), lang)
        return (
            (f"No puedo decirlo: {why}." if lang == "es" else f"I cannot say: {why}."),
            ["analysis.unavailable"],
        )
    ref = sheet.value("analysis.reference_lap")
    keys = ["analysis.lap", "analysis.reference_lap"]
    if routed.corners:
        return _loss_of_corner(sheet, lang, names, routed.corners, lap, keys)
    w = sheet.value("analysis.worst.corner")
    if w is None:
        d, s = th
        text = (
            f"En la vuelta {lap}, frente a la vuelta {ref} (tu mejor), ninguna curva combina una diferencia de "
            f"frenada de al menos {_num(d, 0, lang)} m con una pérdida de al menos {_num(s, 1, lang)} s."
            if lang == "es"
            else f"On lap {lap}, against lap {ref} (your best), no corner combines a braking-point difference of "
            f"at least {_num(d, 0, lang)} m with a loss of at least {_num(s, 1, lang)} s."
        )
        return text, keys
    ref_name = _corner_ref(sheet, int(w), names, lang)
    kind = sheet.value("analysis.worst.kind")
    be = sheet.value("analysis.worst.brake_earlier_m")
    loss = sheet.value("analysis.worst.time_loss_s")
    ms = sheet.value("analysis.worst.min_speed_delta_kmh")
    keys += ["analysis.worst.corner", "analysis.worst.time_loss_s"]
    if kind == "brake" and be is not None:
        keys.append("analysis.worst.brake_earlier_m")
        how = (
            ("antes" if be > 0 else "más tarde")
            if lang == "es"
            else ("earlier" if be > 0 else "later")
        )
        text = (
            f"En la vuelta {lap}, en {ref_name}: pierdes {_num(loss, 2, lang)} s y frenas "
            f"{_num(abs(be), 0, lang)} m {how} que en tu mejor vuelta."
            if lang == "es"
            else f"On lap {lap}, at {ref_name}: you lose {_num(loss, 2, lang)} s and brake "
            f"{_num(abs(be), 0, lang)} m {how} than on your best lap."
        )
    else:
        extra = ""
        if ms is not None and ms <= -5:
            keys.append("analysis.worst.min_speed_delta_kmh")
            extra = (
                f", con velocidad mínima {_num(abs(ms), 0, lang)} km/h menor"
                if lang == "es"
                else f", minimum speed {_num(abs(ms), 0, lang)} km/h lower"
            )
        text = (
            f"En la vuelta {lap}, en {ref_name}: pierdes {_num(loss, 2, lang)} s respecto a tu mejor vuelta{extra}."
            if lang == "es"
            else f"On lap {lap}, at {ref_name}: you lose {_num(loss, 2, lang)} s against your best lap{extra}."
        )
    return text, keys


def _loss_of_corner(sheet, lang, names, corners, lap, keys):
    """What the comparison with the best lap says about the corner(s) the driver named."""
    if len(corners) > 1:
        options = ", ".join(_corner_ref(sheet, n, names, lang) for n in corners)
        return (
            f"Puede ser: {options}. ¿Cuál?"
            if lang == "es"
            else f"It could be: {options}. Which one?"
        ), []
    n = corners[0]
    ref_name = _corner_ref(sheet, n, names, lang)
    k = f"analysis.corners.C{n}"
    td = sheet.value(f"{k}.time_diff_s")
    if td is None:
        text = (
            f"En la vuelta {lap} no tengo una comparación de {ref_name} con tu mejor vuelta: "
            f"esa curva no se pudo medir en una de las dos."
            if lang == "es"
            else f"On lap {lap} I have no comparison of {ref_name} with your best lap: "
            f"that corner could not be measured on one of them."
        )
        return text, keys
    keys = [*keys, f"{k}.time_diff_s"]
    be = sheet.value(f"{k}.brake_earlier_m")
    loss = abs(td)
    if td >= 0:
        gist_es, gist_en = f"pierdes {_num(loss, 2, lang)} s", f"you lose {_num(loss, 2, lang)} s"
    else:
        gist_es, gist_en = f"ganas {_num(loss, 2, lang)} s", f"you gain {_num(loss, 2, lang)} s"
    if be is not None:
        keys.append(f"{k}.brake_earlier_m")
        how_es = "antes" if be > 0 else "más tarde"
        how_en = "earlier" if be > 0 else "later"
        text = (
            f"En la vuelta {lap}, en {ref_name}: {gist_es} y frenas {_num(abs(be), 0, lang)} m {how_es} "
            f"que en tu mejor vuelta."
            if lang == "es"
            else f"On lap {lap}, at {ref_name}: {gist_en} and you brake {_num(abs(be), 0, lang)} m "
            f"{how_en} than on your best lap."
        )
    else:
        text = (
            f"En la vuelta {lap}, en {ref_name}: {gist_es} respecto a tu mejor vuelta."
            if lang == "es"
            else f"On lap {lap}, at {ref_name}: {gist_en} against your best lap."
        )
    return text, keys


def _corner(sheet, lang, track, routed, th):
    if track is not None and track.geometry is None and track.names and routed.corners:
        names, count = track.names, max(track.names)
        if len(routed.corners) == 1:
            d = describe_from_names(routed.corners[0], count, names, lang)
            return (d[0].upper() + d[1:] + "."), []
        options = ", ".join(_corner_ref(sheet, n, names, lang) for n in routed.corners)
        return (
            f"Puede ser: {options}. ¿Cuál?"
            if lang == "es"
            else f"It could be: {options}. Which one?"
        ), []
    if track is None or track.geometry is None or not routed.corners:
        return NO_DATA[lang], []
    names = track.names
    if len(routed.corners) == 1:
        n = routed.corners[0]
        desc = describe_corner(track.geometry, n, lang, names)
        return (desc[0].upper() + desc[1:] + "."), []
    options = ", ".join(_corner_ref(sheet, n, names, lang) for n in routed.corners)
    return (
        f"Puede ser: {options}. ¿Cuál?" if lang == "es" else f"It could be: {options}. Which one?"
    ), []


def _last_alert(sheet, lang, track, routed, th):
    text = sheet.value("alerts.A0.text")
    if text is None:
        return (
            "Todavía no he dado ningún aviso."
            if lang == "es"
            else "I have not given any alert yet."
        ), []
    rule = sheet.value("alerts.A0.rule")
    age = sheet.value("alerts.A0.age_s")
    outcome = sheet.value("alerts.A0.outcome")
    keys = ["alerts.A0.rule", "alerts.A0.text"]
    when = ""
    if age is not None:
        keys.append("alerts.A0.age_s")
        when = f"Hace {_num(age, 0, lang)} s " if lang == "es" else f"{_num(age, 0, lang)} s ago "
    said = (
        ""
        if outcome == "said"
        else (
            f" (no llegó a decirse: {outcome})"
            if lang == "es"
            else f" (it was not spoken: {outcome})"
        )
    )
    if lang == "es":
        return f"{when}el aviso fue «{text}», de la regla {rule}.{said}", keys
    return f"{when}the alert was “{text}”, from the {rule} rule.{said}", keys


def tyre_corners_answer(sheet, lang, names, corner_ref):
    """Shared by the three languages: ``corner_ref(number)`` writes the corner as that language says it."""
    from ..analysis import tyre_corners as TC

    n = sheet.value("tyre_corners.laps")
    need = sheet.value("tyre_corners.laps_needed")
    if n is None:
        return NO_DATA[lang], []
    if n < (need or 0):
        text = {
            "es": f"Todavía no tengo suficientes vueltas con estos neumáticos para decirlo: llevo {n} y necesito {need}.",
            "en": f"I do not have enough laps on these tyres to say yet: I have {n} and need {need}.",
            "de": f"Ich habe noch nicht genug Runden mit diesen Reifen: {n} vorhanden, {need} nötig.",
        }[lang]
        return text, ["tyre_corners.laps", "tyre_corners.laps_needed"]
    keys = ["tyre_corners.laps"]
    parts = []
    first_advice = None
    for axle in ("front", "rear"):
        bits = []
        for j in range(3):
            k = f"tyre_corners.{axle}.R{j}"
            c = sheet.value(f"{k}.corner")
            if c is None:
                break
            share = sheet.value(f"{k}.share_pct")
            phase = sheet.value(f"{k}.phase")
            keys += [f"{k}.corner", f"{k}.share_pct"]
            if j == 0:
                piece = f"{corner_ref(int(c))} ({_num(share, 0, lang)} %, {TC.PHASE_WORDS[lang][phase]})"
                if first_advice is None:
                    first_advice = (axle, phase, int(c))
            else:
                piece = f"{corner_ref(int(c))} ({_num(share, 0, lang)} %)"
            bits.append(piece)
        if bits:
            parts.append((axle, ", ".join(bits)))
    if not parts:
        return NO_DATA[lang], []
    front = next((b for a, b in parts if a == "front"), "")
    rear = next((b for a, b in parts if a == "rear"), "")
    if lang == "es":
        head = (
            f"Es una estimación con el indicador de carga de las {_num(n, 0, lang)} vueltas de estos neumáticos, "
            "no una medida directa. "
        )
        body = f"Delanteros: {front}. Traseros: {rear}."
        advice = TC.ADVICE_ES.get(first_advice[:2]) if first_advice else None
        tail = f" Para bajarlo en {corner_ref(first_advice[2])}: {advice}." if advice else ""
    elif lang == "de":
        head = (
            f"Das ist eine Schätzung mit dem Belastungsindex der {_num(n, 0, lang)} Runden dieser Reifen, "
            "keine direkte Messung. "
        )
        body = f"Vorne: {front}. Hinten: {rear}."
        advice = TC.ADVICE_DE.get(first_advice[:2]) if first_advice else None
        tail = f" Um es bei {corner_ref(first_advice[2])} zu senken: {advice}." if advice else ""
    else:
        head = (
            f"This is an estimate from the load index of the {_num(n, 0, lang)} laps on these tyres, "
            "not a direct measurement. "
        )
        body = f"Fronts: {front}. Rears: {rear}."
        advice = TC.ADVICE_EN.get(first_advice[:2]) if first_advice else None
        tail = f" To reduce it at {corner_ref(first_advice[2])}: {advice}." if advice else ""
    return head + body + tail, keys


def straight_answer(sheet, lang, track, routed, corner_ref):
    """ "The Rahal Straight goes from corner 6 to the entry of the Corkscrew 1": no number that is not a corner."""
    from ..analysis.circuit_names import all_straights, straight_label

    key = next((f.split(":", 1)[1] for f in routed.flags if f.startswith("straight:")), "")
    role, _, name = key.partition(":")
    g = track.geometry if track is not None else None
    st = (
        next(
            (s for s in all_straights(track.track, g) if s.role == role and s.name == name),
            None,
        )
        if track
        else None
    )
    if st is None:
        return NO_DATA[lang], []
    label = straight_label(st, lang)
    label = label[0].upper() + label[1:]
    if g is None or not g.apexes:
        text = {
            "es": f"{label} es una recta de este circuito, pero no tengo su línea para situarla.",
            "en": f"{label} is a straight of this circuit, but I have no racing line to place it.",
            "de": f"{label} ist eine Gerade dieser Strecke, aber ich habe keine Ideallinie, um sie einzuordnen.",
        }[lang]
        return text, []

    def near(f: float) -> int:
        return min(g.apexes, key=lambda c: abs(((f - c.apex_fraction) + 0.5) % 1.0 - 0.5)).number

    a, b = near(st.start), near(st.end)
    text = {
        "es": f"{label} es la recta que va de {corner_ref(a)} a {corner_ref(b)}.",
        "en": f"{label} is the straight that runs from {corner_ref(a)} to {corner_ref(b)}.",
        "de": f"{label} ist die Gerade von {corner_ref(a)} bis {corner_ref(b)}.",
    }[lang]
    return text, []


def _straight(sheet, lang, track, routed, th):
    names = track.names if track is not None else {}
    return straight_answer(sheet, lang, track, routed, lambda c: _corner_ref(sheet, c, names, lang))


def _rival_line(sheet, lang, side: str, sectors: bool = False) -> tuple[str, list[str]] | None:
    k = f"rivals.{side}"
    name = sheet.value(f"{k}.name")
    gap = sheet.value(f"{k}.gap_s")
    laps = sheet.value(f"{k}.laps_diff")
    pos = sheet.value(f"{k}.position")
    if name is None and gap is None and laps is None:
        return None
    keys = [
        f"{k}.{x}"
        for x in ("name", "gap_s", "laps_diff", "position")
        if sheet.value(f"{k}.{x}") is not None
    ]
    who = {"es": "el coche", "en": "the car", "de": "das Auto"}[
        lang if lang in ("es", "de") else "en"
    ]
    if name:
        who = name
    where = {
        "es": {"ahead": "delante", "behind": "detrás"},
        "en": {"ahead": "ahead", "behind": "behind"},
        "de": {"ahead": "vor dir", "behind": "hinter dir"},
    }[lang if lang in ("es", "de") else "en"][side]
    p = f" (P{pos})" if pos else ""
    if laps:
        n = int(laps)
        text = {
            "es": f"{where.capitalize()}: {who}{p}, a {n} vuelta{'s' if n != 1 else ''}.",
            "de": f"{where.capitalize()}: {who}{p}, {n} Runde{'n' if n != 1 else ''}.",
        }.get(lang, f"{where.capitalize()}: {who}{p}, {n} lap{'s' if n != 1 else ''}.")
    elif gap is not None:
        text = {
            "es": f"{where.capitalize()}: {who}{p}, a {_num(gap, 1, lang)} s.",
            "de": f"{where.capitalize()}: {who}{p}, {_num(gap, 1, lang)} s.",
        }.get(lang, f"{where.capitalize()}: {who}{p}, {_num(gap, 1, lang)} s.")
    else:
        text = f"{where.capitalize()}: {who}{p}."
    closing = sheet.value(f"{k}.closing_s")
    if closing is not None:
        is_closing = sheet.value(f"{k}.is_closing")
        keys += [f"{k}.closing_s", f"{k}.is_closing"]
        verb = {
            "es": ("se acerca" if is_closing else "se aleja"),
            "en": ("is closing in" if is_closing else "is pulling away"),
            "de": ("kommt näher" if is_closing else "fährt weg"),
        }[lang if lang in ("es", "de") else "en"]
        unit = {
            "es": "en el último medio minuto",
            "en": "over the last half minute",
            "de": "in der letzten halben Minute",
        }[lang if lang in ("es", "de") else "en"]
        text += f" {verb.capitalize()}: {_num(closing, 1, lang)} s {unit}."
    last = sheet.value(f"{k}.last_lap_s")
    if last is not None:
        keys.append(f"{k}.last_lap_s")
        text += {
            "es": f" Su última vuelta: {_clock(last, lang)}.",
            "de": f" Seine letzte Runde: {_clock(last, lang)}.",
        }.get(lang, f" Last lap: {_clock(last, lang)}.")
    best = sheet.value(f"{k}.best_lap_s")
    if best is not None:
        keys.append(f"{k}.best_lap_s")
        text += {
            "es": f" Su mejor vuelta: {_clock(best, lang)}.",
            "de": f" Seine schnellste Runde: {_clock(best, lang)}.",
        }.get(lang, f" Best lap: {_clock(best, lang)}.")
    if sectors:
        deltas = [(n, sheet.value(f"{k}.sector{n}_delta_s")) for n in (1, 2, 3, 4, 5)]
        deltas = [(n, d) for n, d in deltas if d is not None]
        if deltas:
            keys += [f"{k}.sector{n}_delta_s" for n, _ in deltas]
            bits = []
            for n, d in deltas:
                if d > 0:
                    bits.append(
                        {
                            "es": f"sector {n}: pierdes {_num(d, 2, lang)} s",
                            "de": f"Sektor {n}: du verlierst {_num(d, 2, lang)} s",
                        }.get(lang, f"sector {n}: you lose {_num(d, 2, lang)} s")
                    )
                else:
                    bits.append(
                        {
                            "es": f"sector {n}: ganas {_num(-d, 2, lang)} s",
                            "de": f"Sektor {n}: du gewinnst {_num(-d, 2, lang)} s",
                        }.get(lang, f"sector {n}: you gain {_num(-d, 2, lang)} s")
                    )
            text += (
                " "
                + {"es": "En la última vuelta, ", "de": "In der letzten Runde: "}.get(
                    lang, "On the last lap, "
                )
                + "; ".join(bits)
                + "."
            )
    return text, keys


def _leader_line(sheet, lang) -> tuple[str, list[str]] | None:
    if sheet.value("rivals.i_lead"):
        return {"es": "Vas primero.", "de": "Du führst.", "en": "You are leading."}.get(
            lang, "You are leading."
        ), ["rivals.i_lead"]
    name = sheet.value("rivals.leader.name")
    if name is None:
        return None
    keys = ["rivals.leader.name"]
    gap = sheet.value("rivals.leader.gap_s")
    laps = sheet.value("rivals.leader.laps_diff")
    best = sheet.value("rivals.leader.best_lap_s")
    if laps:
        n = int(laps)
        keys.append("rivals.leader.laps_diff")
        text = {
            "es": f"El líder es {name}, a {n} vuelta{'s' if n != 1 else ''}.",
            "de": f"Der Führende ist {name}, {n} Runde{'n' if n != 1 else ''} voraus.",
        }.get(lang, f"The leader is {name}, {n} lap{'s' if n != 1 else ''} ahead.")
    elif gap is not None:
        keys.append("rivals.leader.gap_s")
        text = {
            "es": f"El líder es {name}, a {_num(gap, 1, lang)} s.",
            "de": f"Der Führende ist {name}, {_num(gap, 1, lang)} s vor dir.",
        }.get(lang, f"The leader is {name}, {_num(gap, 1, lang)} s ahead.")
    else:
        text = {"es": f"El líder es {name}.", "de": f"Der Führende ist {name}."}.get(
            lang, f"The leader is {name}."
        )
    if best is not None:
        keys.append("rivals.leader.best_lap_s")
        text += {
            "es": f" Su mejor vuelta: {_clock(best, lang)}.",
            "de": f" Seine schnellste Runde: {_clock(best, lang)}.",
        }.get(lang, f" Best lap: {_clock(best, lang)}.")
    return text, keys


def _pf(sheet, key: str, value: float, unit: str = "") -> None:
    """A number the planner computed, as a fact with its unit: the grounding check then knows it (and its unit)."""
    sheet.add(Fact(f"plan.calc.{key}", round(float(value), 2), unit))


def _race_plan(sheet, lang, track, routed, th):
    """Where to stop: the planner with the rules the driver stated (mandatory stop, window), asking for what is missing."""
    from ..analysis.plan import FuelProfile, plan_stops, race_laps

    es = lang == "es"
    per = sheet.value("fuel.per_lap_l")
    if per is None:
        return (
            "Para el plan necesito el consumo por vuelta: al menos dos vueltas cronometradas completas."
            if es
            else "For the plan I need the fuel per lap: at least two timed laps."
        ), []
    flags = {f.split(":")[0]: f.split(":")[1] for f in routed.flags if ":" in f}
    lap_s = sheet.value("laps.best.time_s") or sheet.value("laps.last.time_s")
    total = None
    if "laps" in flags:
        total = int(flags["laps"])
    elif "minutes" in flags:
        total = race_laps(minutes=float(flags["minutes"]), lap_time_s=lap_s)
    elif sheet.value("session.laps_total"):
        total = int(sheet.value("session.laps_total"))
    if total is None:
        return (
            "¿De cuántas vueltas o minutos es la carrera?"
            if es
            else "How many laps or minutes is the race?"
        ), []
    stop_rule = sheet.value("plan.mandatory_stop")
    if stop_rule is None:
        return (
            "¿Hay parada obligatoria? Dime ninguna, una, o varias, y si hay ventana de paradas."
            if es
            else "Is there a mandatory stop? Tell me none, one or several, and whether there is a pit window."
        ), []
    prof = FuelProfile(
        float(per),
        float(sheet.value("fuel.spread_l", 0.0)),
        int(sheet.value("fuel.profile_laps", 0)),
    )
    tank = sheet.value("fuel.tank_l")
    if not tank:
        return (
            "No sé la capacidad del depósito de este coche: dime cuántos litros caben."
            if es
            else "I do not know this car's tank size: tell me how many litres it holds."
        ), []
    window = None
    if sheet.value("plan.window_from") is not None:
        window = (int(sheet.value("plan.window_from")), int(sheet.value("plan.window_to")))
    stops = {"none": 0, "one": 1}.get(stop_rule)
    if stops is None:  # several: at least the ones the tank needs
        for k in range(1, 6):
            if plan_stops(prof, total, float(tank), stops=k, window=window).feasible:
                stops = k
                break
        stops = stops if stops is not None else 1
    plan = plan_stops(prof, total, float(tank), stops=stops, window=window)
    _pf(sheet, "race_laps", total, "laps")
    _pf(sheet, "stops", stops)
    _pf(sheet, "tank", tank, "L")
    _pf(sheet, "per_lap", prof.per_lap_l, "L")
    _pf(sheet, "spread", prof.spread_l, "L")
    _pf(sheet, "profile_laps", prof.laps, "laps")
    if not plan.feasible:
        return (
            f"Con {total} vueltas no me sale un plan de {stops} parada{'s' if stops != 1 else ''}: {plan.reason}."
            if es
            else f"For {total} laps I cannot make a {stops}-stop plan: {plan.reason}."
        ), ["fuel.per_lap_l", "fuel.tank_l"]
    lengths = [str(st.laps) for st in plan.stints]
    stints = (
        " y ".join(lengths)
        if es and len(lengths) == 2
        else " and ".join(lengths)
        if len(lengths) == 2
        else ", ".join(lengths)
    )
    for i, st in enumerate(plan.stints):
        _pf(sheet, f"stint{i}_laps", st.laps, "laps")
        _pf(sheet, f"stint{i}_fuel", st.fuel_start_l, "L")
    for i, x in enumerate(plan.stop_laps):
        _pf(sheet, f"stop{i}_lap", x, "laps")
    said = (
        f"consumo {_num(prof.per_lap_l, 2, lang)} litros por vuelta, de {prof.laps} vueltas, dispersión {_num(prof.spread_l, 2, lang)}"
        if es
        else f"{_num(prof.per_lap_l, 2, lang)} litres per lap, from {prof.laps} laps, spread {_num(prof.spread_l, 2, lang)}"
    )
    if stops == 0:
        text = (
            f"Sin paradas: {total} vueltas con {_num(plan.stints[0].fuel_start_l, 1, lang)} litros al salir "
            f"({said})."
            if es
            else f"No stops: {total} laps with {_num(plan.stints[0].fuel_start_l, 1, lang)} litres at the start ({said})."
        )
    else:
        stops_at = (
            " y ".join(str(x) for x in plan.stop_laps)
            if es
            else " and ".join(str(x) for x in plan.stop_laps)
        )
        text = (
            f"{stops} parada{'s' if stops != 1 else ''}, en la vuelta {stops_at}: tandas de {stints} vueltas; "
            f"sales con {_num(plan.stints[0].fuel_start_l, 1, lang)} litros ({said})."
            if es
            else f"{stops} stop{'s' if stops != 1 else ''}, on lap {stops_at}: stints of {stints} laps; you start with "
            f"{_num(plan.stints[0].fuel_start_l, 1, lang)} litres ({said})."
        )
    return text, ["fuel.per_lap_l", "fuel.tank_l"]


def _fuel_plan(sheet, lang, track, routed, th):
    """The fuel planner (analysis/plan.py) in words: what to load for a length, or whether the fuel reaches the end."""
    from ..analysis.plan import FuelProfile, fuel_to_finish, race_laps, saving_per_lap

    per = sheet.value("fuel.per_lap_l")
    if per is None:
        return {
            "es": "Todavía no tengo el consumo por vuelta: hacen falta al menos dos vueltas cronometradas completas.",
            "de": "Ich kenne den Verbrauch pro Runde noch nicht: es braucht mindestens zwei gezeitete Runden.",
        }.get(
            lang, "I do not know the consumption per lap yet: at least two timed laps are needed."
        ), []
    prof = FuelProfile(
        float(per),
        float(sheet.value("fuel.spread_l", 0.0)),
        int(sheet.value("fuel.profile_laps", 0)),
    )
    keys = ["fuel.per_lap_l"]
    flags = {f.split(":")[0]: f.split(":")[1] for f in routed.flags if ":" in f}
    lap_s = sheet.value("laps.best.time_s") or sheet.value("laps.last.time_s")
    laps = None
    said = ""
    if "laps" in flags:
        laps = int(flags["laps"])
    elif "minutes" in flags:
        laps = race_laps(minutes=float(flags["minutes"]), lap_time_s=lap_s, extra_lap=True)
        if laps is None:
            return {
                "es": "Para convertir minutos en vueltas necesito una vuelta cronometrada.",
                "de": "Um Minuten in Runden umzurechnen, brauche ich eine gezeitete Runde.",
            }.get(lang, "To turn minutes into laps I need a timed lap."), keys
        _pf(sheet, "race_laps", laps, "laps")
        said = {
            "es": f"{flags['minutes']} minutos son {laps} vueltas con tu ritmo, contando la de más. ",
            "de": f"{flags['minutes']} Minuten sind {laps} Runden mit deinem Tempo, die Extrarunde eingerechnet. ",
        }.get(
            lang,
            f"{flags['minutes']} minutes are {laps} laps at your pace, counting the extra one. ",
        )
    if laps is not None:
        plan = fuel_to_finish(prof, laps)
        _pf(sheet, "fuel_needed", plan.litres, "L")
        _pf(sheet, "margin", plan.margin_l, "L")
        _pf(sheet, "per_lap", prof.per_lap_l, "L")
        _pf(sheet, "spread", prof.spread_l, "L")
        _pf(sheet, "profile_laps", prof.laps, "laps")
        text = said + {
            "es": f"Para {laps} vueltas carga unos {_num(plan.litres, 1, lang)} litros: "
            f"{_num(prof.per_lap_l, 2, lang)} por vuelta y {_num(plan.margin_l, 1, lang)} de margen "
            f"(con {prof.laps} vueltas de datos).",
            "de": f"Für {laps} Runden lade etwa {_num(plan.litres, 1, lang)} Liter: "
            f"{_num(prof.per_lap_l, 2, lang)} pro Runde und {_num(plan.margin_l, 1, lang)} Reserve "
            f"(aus {prof.laps} Runden).",
        }.get(
            lang,
            f"For {laps} laps load about {_num(plan.litres, 1, lang)} litres: {_num(prof.per_lap_l, 2, lang)} per lap "
            f"and {_num(plan.margin_l, 1, lang)} margin (from {prof.laps} laps).",
        )
        return text, keys
    # no length named: does the fuel reach the end of this session?
    fuel_now = sheet.value("fuel.litres")
    total = sheet.value("session.laps_total")
    done = sheet.value("session.laps_completed", 0)
    if fuel_now is None or not total or total - done <= 0:
        return {
            "es": f"Con {_num(per, 2, lang)} litros por vuelta; dime cuántas vueltas o minutos y te digo cuánto cargar.",
            "de": f"Bei {_num(per, 2, lang)} Litern pro Runde; nenne Runden oder Minuten und ich sage dir, wie viel du laden sollst.",
        }.get(
            lang,
            f"At {_num(per, 2, lang)} litres per lap; give me laps or minutes and I will say how much to load.",
        ), keys
    left = int(total - done)
    save = saving_per_lap(float(fuel_now), left, prof)
    _pf(sheet, "laps_left", left, "laps")
    _pf(sheet, "saving", abs(save), "L")
    keys += ["fuel.litres", "session.laps_total"]
    if save > 0.02:
        text = {
            "es": f"Quedan {left} vueltas y {_num(fuel_now, 1, lang)} litros: ahorra {_num(save, 2, lang)} litros por vuelta para llegar.",
            "de": f"Es sind noch {left} Runden und {_num(fuel_now, 1, lang)} Liter: spare {_num(save, 2, lang)} Liter pro Runde.",
        }.get(
            lang,
            f"{left} laps to go and {_num(fuel_now, 1, lang)} litres: save {_num(save, 2, lang)} litres per lap to make it.",
        )
    else:
        text = {
            "es": f"Quedan {left} vueltas y {_num(fuel_now, 1, lang)} litros: llegas con margen, puedes gastar {_num(-save, 2, lang)} litros más por vuelta.",
            "de": f"Es sind noch {left} Runden und {_num(fuel_now, 1, lang)} Liter: du kommst mit Reserve an, {_num(-save, 2, lang)} Liter mehr pro Runde sind möglich.",
        }.get(
            lang,
            f"{left} laps to go and {_num(fuel_now, 1, lang)} litres: you make it with room, {_num(-save, 2, lang)} litres more per lap is possible.",
        )
    return text, keys


def _weather(sheet, lang, track, routed, th):
    air = sheet.value("weather.air_c")
    road = sheet.value("weather.road_c")
    parts, keys = [], []
    if air is not None:
        parts.append(
            {
                "es": f"el aire a {_num(air, 0, lang)} °C",
                "de": f"die Luft {_num(air, 0, lang)} °C",
            }.get(lang, f"the air at {_num(air, 0, lang)} °C")
        )
        keys.append("weather.air_c")
    if road is not None:
        parts.append(
            {
                "es": f"el asfalto a {_num(road, 0, lang)} °C",
                "de": f"der Asphalt {_num(road, 0, lang)} °C",
            }.get(lang, f"the track at {_num(road, 0, lang)} °C")
        )
        keys.append("weather.road_c")
    if not parts:
        return NO_DATA[lang], []
    head = {"es": "Tenemos ", "de": "Wir haben ", "en": "We have "}.get(lang, "We have ")
    return head + {"es": " y ", "de": " und "}.get(lang, " and ").join(parts) + ".", keys


def _rivals(sheet, lang, track, routed, th):
    if "leader" in routed.flags:
        got = _leader_line(sheet, lang)
        if got is not None:
            return got
    sides = [s for s in ("ahead", "behind") if s in routed.flags] or ["ahead", "behind"]
    out: list[str] = []
    keys: list[str] = []
    for side in sides:
        got = _rival_line(sheet, lang, side, "sectors" in routed.flags)
        if got is not None:
            out.append(got[0])
            keys += got[1]
    if not out:
        return {
            "es": "No veo a otros coches: necesito el puente de CSP activo (la app Pit Wall Bridge) y una sesión con rivales.",
            "de": "Ich sehe keine anderen Autos: die CSP-Brücke (Pit Wall Bridge) muss aktiv sein und die Sitzung Gegner haben.",
        }.get(
            lang,
            "I do not see other cars: the CSP bridge (Pit Wall Bridge app) must be on and the session must have rivals.",
        ), []
    return " ".join(out), keys


def _corner_names(sheet, lang, track, routed, th):
    # "Curva 7" is the table's placeholder for an unnamed corner, not a name
    names = {
        a: n
        for a, n in (track.names if track is not None else {}).items()
        if not re.fullmatch(r"(?i)(curva|corner|turn|kurve)\s+\d+", n.strip())
    }
    if not names:
        return {
            "es": "De este circuito no tengo nombres de curvas.",
            "de": "Von dieser Strecke habe ich keine Kurvennamen.",
        }.get(lang, "I have no corner names for this circuit."), []
    official = getattr(track.geometry, "official", None)
    items = []
    for apex in sorted(names):
        shown = apex if official is None else official.get(apex)
        items.append(f"{shown}: {names[apex]}" if shown is not None else names[apex])
    head = {"es": "Las curvas con nombre son", "de": "Die benannten Kurven sind"}.get(
        lang, "The named corners are"
    )
    return f"{head} " + "; ".join(items) + ".", []


def _tyre_corners(sheet, lang, track, routed, th):
    names = track.names if track is not None else {}
    return tyre_corners_answer(sheet, lang, names, lambda c: _corner_ref(sheet, c, names, lang))


def _time_left(sheet, lang, track, routed, th):
    left = sheet.value("session.time_left_s")
    if left is not None and left > 0:
        mm, ss = divmod(int(left), 60)
        return (f"Quedan {mm}:{ss:02d}." if lang == "es" else f"{mm}:{ss:02d} left."), [
            "session.time_left_s"
        ]
    timed = sheet.value("session.timed")
    if timed is False:
        return (
            "Esta sesión no tiene límite de tiempo: no hay un tiempo restante que dar."
            if lang == "es"
            else "This session has no time limit: there is no time left to give."
        ), ["session.timed"]
    return (
        "El juego no me da el tiempo restante de esta sesión."
        if lang == "es"
        else "The game does not give me the time left of this session."
    ), []


def _pit_decision(sheet, lang):
    """A stop question before the strategy engine exists: say what the tyres measure, never decide."""
    wear = {w: sheet.value(f"tyres.{w}.wear_pct") for w in ("FL", "FR", "RL", "RR")}
    wear = {w: v for w, v in wear.items() if v is not None}
    if not wear:
        return None
    keys = [f"tyres.{w}.wear_pct" for w in wear]
    worst = min(wear, key=wear.get)
    if lang == "es":
        return (
            f"La decisión de parada todavía no la calculo: el plan de carrera llega en una fase posterior. "
            f"Lo que mido: el neumático más gastado es el {_WHEEL[lang][worst]}, con "
            f"{_num(wear[worst], 1, lang)} % de vida; el mejor tiene {_num(max(wear.values()), 1, lang)} %."
        ), keys
    return (
        f"I do not compute the stop decision yet: the race plan comes in a later phase. "
        f"What I measure: the most worn tyre is the {_WHEEL[lang][worst]} one, with "
        f"{_num(wear[worst], 1, lang)} % left; the best has {_num(max(wear.values()), 1, lang)} %."
    ), keys


def _not_yet(sheet, lang, track, routed, th):
    if "pit_decision" in routed.flags:
        got = _pit_decision(sheet, lang)
        if got is not None:
            return got
    if lang == "es":
        return (
            "Los consejos de reglaje y la estrategia de carrera todavía no están disponibles: "
            "llegan en una fase posterior del proyecto. Ahora puedo hablarte de lo que mide el coche en esta sesión."
        ), []
    return (
        "Setup advice and race strategy are not available yet: they come in a later phase. "
        "For now I can talk about what the car measures in this session."
    ), []


def _unknown(sheet, lang, track, routed, th):
    if lang == "es":
        return (
            "No tengo ese dato: solo veo la telemetría de tu coche y de la sesión, "
            "no el clima ni a los demás pilotos."
        ), []
    return (
        "I do not have that data: I only see your car's telemetry and the session, "
        "not the weather or other drivers."
    ), []


_BUILDERS = {
    Intent.FUEL: _fuel,
    Intent.LAPS: _laps,
    Intent.TYRES: _tyres,
    Intent.BRAKES: _brakes,
    Intent.SESSION: _session,
    Intent.PENALTY: _penalty,
    Intent.LOSS: _loss,
    Intent.CORNER: _corner,
    Intent.LAST_ALERT: _last_alert,
    Intent.UNKNOWN_DATA: _unknown,
    Intent.NOT_YET: _not_yet,
    Intent.TIME_LEFT: _time_left,
    Intent.TYRE_CORNERS: _tyre_corners,
    Intent.CORNER_NAMES: _corner_names,
    Intent.RIVALS: _rivals,
    Intent.WEATHER: _weather,
    Intent.FUEL_PLAN: _fuel_plan,
    Intent.RACE_PLAN: _race_plan,
    Intent.STRAIGHT: _straight,
}
