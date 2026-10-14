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
from .facts import FactSheet, norm


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
        "el de adelante",
        "el de atras",
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
_KEYS[Intent.NOT_YET] = (
    "setup",
    "reglaje",
    "reglajes",
    "configuracion del coche",
    "estrategia",
    "cuando debo parar",
    "cuando parar",
    "cuando paro",
    "plan de carrera",
    "strategy",
    "when should i pit",
    "when to pit",
    "pit strategy",
    "tyre strategy",
    "estrategia de neumaticos",
)
_KEYS[Intent.TIME_LEFT] = (
    "cuanto tiempo queda",
    "cuanto falta",
    "cuanto queda de sesion",
    "cuanto queda de carrera",
    "time left",
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
    Intent.LAST_ALERT,
    Intent.LOSS,
    Intent.PENALTY,
    Intent.FUEL,
    Intent.TYRES,
    Intent.BRAKES,
    Intent.LAPS,
    Intent.SESSION,
    Intent.TIME_LEFT,
    Intent.NOT_YET,
    Intent.UNKNOWN_DATA,
)


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
    # setup / strategy is a question on its own: a clear "not yet" instead of a half answer
    if Intent.NOT_YET in found:
        return Routed((Intent.NOT_YET,), corners)
    # a corner or an alert explanation is a question on its own
    for solo in (Intent.CORNER, Intent.LAST_ALERT):
        if solo in found:
            return Routed((solo,), corners)
    # a braking/losing question already covers "frenadas": do not also dump the brake temperatures
    if Intent.LOSS in found and Intent.BRAKES in found:
        found.remove(Intent.BRAKES)
    hot, cold = (DE.HOTTEST, DE.COLDEST) if de else (_HOTTEST, _COLDEST)
    flags = tuple(f for f, words in (("hottest", hot), ("coldest", cold)) if _has(q, words))
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
    base = f"la curva {number}" if lang == "es" else f"corner {number}"
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
    for w in ("FL", "FR", "RL", "RR"):
        core, p, wear = (
            sheet.value(f"tyres.{w}.{k}") for k in ("core_c", "pressure_psi", "wear_pct")
        )
        if core is None and p is None:
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
        return (
            f"Tienes {_num(t, 1, lang)} s de penalización según el juego "
            "(la unidad de ese campo aún no está verificada)."
        ), ["penalties.time_s"]
    return (
        f"The game reports {_num(t, 1, lang)} s of penalty "
        "(the unit of that field is not verified yet)."
    ), ["penalties.time_s"]


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


def _not_yet(sheet, lang, track, routed, th):
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
}
