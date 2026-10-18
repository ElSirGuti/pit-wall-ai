"""The spoken version of the debrief: 3 to 4 sentences, every number checked against the facts.

A local template always works. When a model is available it phrases the same facts (in the persona's style) and the
sentence goes through the grounding check of the chat; anything with a number that is not in the facts falls back to
the template. ``docs/PHASE7_PLAN.md`` step 2.
"""

from __future__ import annotations

import logging

from ..chat import grounding as G
from ..chat.facts import Fact, FactSheet
from ..engineer.llm import LLMRequest
from .debrief import Debrief

log = logging.getLogger(__name__)

_PROMPT = {
    "es": (
        "Eres un ingeniero de carreras por radio. Resume la sesión del piloto en 3 o 4 frases habladas, "
        "como mucho 25 segundos. Elige solo lo más importante (resultado, ritmo, dónde se fue el tiempo, "
        "neumáticos, incidentes), usa EXCLUSIVAMENTE las cifras del contexto tal como están y no inventes ninguna. "
        "Sin listas ni títulos. No cuentes nada que no esté contado en el contexto (penalizaciones, contactos, vueltas). "
        "Escribe las cifras con dígitos y sin redondear (1:17,737, 0,56 s), nunca en letras."
    ),
    "en": (
        "You are a race engineer on the radio. Summarise the driver's session in 3 or 4 spoken sentences, at most "
        "25 seconds. Pick only the most important (result, pace, where the time went, tyres, incidents), use ONLY "
        "the figures in the context exactly as they are and invent none. No lists, no headings. Count nothing that "
        "is not counted in the context (penalties, contacts, laps). "
        "Write the figures as digits, unrounded (1:17.737, 0.56 s), never as words."
    ),
    "de": (
        "Du bist ein Renningenieur im Funk. Fasse die Sitzung des Fahrers in 3 oder 4 gesprochenen Sätzen "
        "zusammen, höchstens 25 Sekunden. Wähle nur das Wichtigste (Ergebnis, Tempo, wo die Zeit blieb, Reifen, "
        "Zwischenfälle), verwende AUSSCHLIESSLICH die Zahlen aus dem Kontext genau so und erfinde keine. "
        "Keine Listen, keine Überschriften. Zähle nichts, was im Kontext nicht gezählt ist (Strafen, Kontakte, Runden). "
        "Schreibe die Zahlen als Ziffern, ungerundet (1:17,737, 0,56 s), nie als Wörter."
    ),
}


def _time_numbers(ms: float) -> tuple[float, ...]:
    return (round(ms / 1000.0, 3), float(ms))


def facts_sheet(d: Debrief, lang: str = "es") -> FactSheet:
    """The debrief facts as a fact sheet: what the model sees and what its sentences are checked against."""
    sheet = FactSheet(lang=lang)
    add = sheet.add
    f = d.facts
    r = f.get("result", {})
    add(Fact("result.session", r.get("session", "")))
    add(Fact("result.laps", r.get("laps", 0), "laps"))
    for k in ("position_start", "position_end"):
        if k in r:
            add(Fact(f"result.{k}", r[k]))
    p = f.get("pace")
    if p:
        add(
            Fact(
                "pace.best_s",
                round(p["best_ms"] / 1000.0, 3),
                "s",
                numbers=_time_numbers(p["best_ms"]),
            )
        )
        add(Fact("pace.valid_laps", p["valid_laps"], "laps"))
        if "ideal_ms" in p:
            add(
                Fact(
                    "pace.ideal_s",
                    round(p["ideal_ms"] / 1000.0, 3),
                    "s",
                    numbers=_time_numbers(p["ideal_ms"]),
                )
            )
            add(Fact("pace.ideal_gap_s", round(p["ideal_gap_ms"] / 1000.0, 3), "s"))
        if "consistency_ms" in p:
            add(Fact("pace.consistency_s", round(p["consistency_ms"] / 1000.0, 3), "s"))
    for i, c in enumerate(f.get("time_loss", {}).get("corners", [])):
        k = f"time_loss.{i}"
        if c.get("corner") is not None:
            add(Fact(f"{k}.corner", c["corner"]))
        if c.get("name"):
            add(Fact(f"{k}.name", c["name"]))
        add(Fact(f"{k}.mean_loss_s", c["mean_loss_s"], "s"))
        add(Fact(f"{k}.laps", c["laps"], "laps"))
        if c.get("brake_earlier_m") is not None:
            add(Fact(f"{k}.brake_earlier_m", abs(c["brake_earlier_m"]), "m"))
    ty = f.get("tyres", {})
    for w, v in ty.get("wear_used", {}).items():
        add(Fact(f"tyres.wear_used.{w}", v))
    ci = ty.get("corner_index")
    if ci:
        add(Fact("tyres.corner_index.is_estimate", True))
        for axle in ("front", "rear"):
            for i, c in enumerate(ci.get(axle, [])):
                if c.get("corner") is not None:
                    add(Fact(f"tyres.corner_index.{axle}.{i}.corner", c["corner"]))
                if c.get("name"):
                    add(Fact(f"tyres.corner_index.{axle}.{i}.name", c["name"]))
    vs = f.get("vs_last")
    if vs:
        add(Fact("vs_last.best_delta_s", round(abs(vs["best_delta_ms"]) / 1000.0, 3), "s"))
        add(Fact("vs_last.is_faster", vs["best_delta_ms"] < 0))
    inc = f.get("incidents")
    if inc:
        add(Fact("incidents.laps", len(inc["laps_with_incidents"]), "laps"))
    pe = f.get("penalties", {})
    if "max_penalty_s" in pe:
        add(Fact("penalties.max_s", pe["max_penalty_s"], "s"))
        add(Fact("penalties.count", pe.get("penalties", 1)))
    if "contacts" in pe:
        add(Fact("penalties.contacts", pe["contacts"]))
        add(Fact("penalties.worst_contact_kmh", pe["worst_contact"], "km/h"))
    return sheet


def _clock(ms: int, lang: str) -> str:
    m, s = divmod(ms / 1000.0, 60)
    t = f"{int(m)}:{s:06.3f}"
    return t.replace(".", ",") if lang != "en" else t


def _c(x: float, nd: int, lang: str) -> str:
    s = f"{x:.{nd}f}"
    return s.replace(".", ",") if lang != "en" else s


def _corner_say(c: dict, lang: str) -> str:
    num = c.get("corner")
    name = c.get("name") or ""
    if name.lower().startswith(("curva ", "corner ", "kurve ")):
        name = ""
    word = {"es": "la curva", "en": "corner", "de": "Kurve"}[lang if lang in ("es", "de") else "en"]
    if name and num is not None:
        return f"{name}, {word} {num}"
    if name:
        return name
    return (
        f"{word} {num}"
        if num is not None
        else {"es": "una curva", "en": "a corner", "de": "eine Kurve"}[
            lang if lang in ("es", "de") else "en"
        ]
    )


def local_summary(d: Debrief, lang: str = "es") -> str:
    """The template version: the same facts in plain sentences (what is said when there is no model)."""
    lang = lang if lang in ("es", "de") else "en"
    f = d.facts
    r = f.get("result", {})
    sentences: list[str] = []
    kind = r.get("session", "")
    laps = r.get("laps", 0)
    if kind == "race" and "position_end" in r:
        gained = r["position_start"] - r["position_end"]
        sentences.append(
            {
                "es": f"Carrera terminada en la posición {r['position_end']}, saliendo de la {r['position_start']}"
                + (
                    f": ganaste {gained} puesto{'s' if gained != 1 else ''}."
                    if gained > 0
                    else (f": perdiste {-gained}." if gained < 0 else ".")
                ),
                "en": f"Race finished in position {r['position_end']}, starting from {r['position_start']}"
                + (
                    f": {gained} place{'s' if gained != 1 else ''} gained."
                    if gained > 0
                    else (f": {-gained} lost." if gained < 0 else ".")
                ),
                "de": f"Rennen auf Platz {r['position_end']} beendet, Start von {r['position_start']}.",
            }[lang]
        )
    elif kind == "qualify" and "position_end" in r:
        sentences.append(
            {
                "es": f"Clasificación terminada en la posición {r['position_end']}.",
                "en": f"Qualifying finished in position {r['position_end']}.",
                "de": f"Qualifying auf Platz {r['position_end']} beendet.",
            }[lang]
        )
    elif laps:
        sentences.append(
            {
                "es": f"Sesión de {laps} vueltas.",
                "en": f"A session of {laps} laps.",
                "de": f"Eine Sitzung mit {laps} Runden.",
            }[lang]
        )
    p = f.get("pace")
    if p:
        best = _clock(p["best_ms"], lang)
        gap = p.get("ideal_gap_ms", 0) / 1000.0
        if gap >= 0.1:
            sentences.append(
                {
                    "es": f"Tu mejor vuelta fue {best}, a {_c(gap, 1, lang)} segundos de tu vuelta ideal.",
                    "en": f"Your best lap was {best}, {_c(gap, 1, lang)} seconds off your ideal lap.",
                    "de": f"Deine schnellste Runde war {best}, {_c(gap, 1, lang)} Sekunden von deiner Idealrunde entfernt.",
                }[lang]
            )
        else:
            sentences.append(
                {
                    "es": f"Tu mejor vuelta fue {best}.",
                    "en": f"Your best lap was {best}.",
                    "de": f"Deine schnellste Runde war {best}.",
                }[lang]
            )
        vs = f.get("vs_last")
        if vs and vs.get("same_compound"):
            d_s = abs(vs["best_delta_ms"]) / 1000.0
            faster = vs["best_delta_ms"] < 0
            if d_s < 0.05:
                tail = {
                    "es": ", igual que la última vez.",
                    "en": ", the same as last time.",
                    "de": ", wie beim letzten Mal.",
                }[lang]
            else:
                tail = {
                    "es": f", {_c(d_s, 1, lang)} segundos {'más rápida' if faster else 'más lenta'} que la última vez.",
                    "en": f", {_c(d_s, 1, lang)} seconds {'faster' if faster else 'slower'} than last time.",
                    "de": f", {_c(d_s, 1, lang)} Sekunden {'schneller' if faster else 'langsamer'} als beim letzten Mal.",
                }[lang]
            sentences[-1] = sentences[-1].rstrip(".") + tail
    tl = f.get("time_loss", {}).get("corners", [])
    if tl:
        c = tl[0]
        say = _corner_say(c, lang)
        sentences.append(
            {
                "es": f"Donde más tiempo pierdes es {say}: {_c(c['mean_loss_s'], 1, lang)} segundos de media por vuelta.",
                "en": f"Where you lose most is {say}: {_c(c['mean_loss_s'], 1, lang)} seconds a lap on average.",
                "de": f"Am meisten Zeit verlierst du in {say}: {_c(c['mean_loss_s'], 1, lang)} Sekunden pro Runde im Schnitt.",
            }[lang]
        )
    pe = f.get("penalties", {})
    bits = []
    if "max_penalty_s" in pe:
        bits.append(
            {
                "es": f"una penalización de {pe['max_penalty_s']:.0f} segundos",
                "en": f"a {pe['max_penalty_s']:.0f} second penalty",
                "de": f"eine Strafe von {pe['max_penalty_s']:.0f} Sekunden",
            }[lang]
        )
    if "contacts" in pe:
        n = pe["contacts"]
        bits.append(
            {
                "es": f"{n} contacto{'s' if n != 1 else ''}",
                "en": f"{n} contact{'s' if n != 1 else ''}",
                "de": f"{n} Kontakt{'e' if n != 1 else ''}",
            }[lang]
        )
    if bits:
        sentences.append(
            {"es": "Hubo ", "en": "There was ", "de": "Es gab "}[lang]
            + {"es": " y ", "en": " and ", "de": " und "}[lang].join(bits)
            + "."
        )
    return " ".join(sentences[:4])


def spoken_summary(
    d: Debrief, cfg, client=None, *, lang: str = "es", persona_suffix: str = ""
) -> tuple[str, str]:
    """(text, source): the model's phrasing when it passes the check, otherwise the template."""
    local = local_summary(d, lang)
    if client is None or not d.facts:
        return local, "local"
    sheet = facts_sheet(d, lang)
    request = LLMRequest(
        system=_PROMPT.get(lang, _PROMPT["en"]) + (" " + persona_suffix if persona_suffix else ""),
        user={"es": "Resume la sesión.", "de": "Fasse die Sitzung zusammen."}.get(
            lang, "Summarise the session."
        ),
        context=sheet.context(),
    )
    try:
        resp = client.ask(request, urgent=False)
    except Exception:  # noqa: BLE001 - the debrief must never fail because of the model
        log.exception("the model failed for the debrief")
        return local, "local"
    if resp is None or not resp.text.strip():
        return local, "local"
    report = G.check(resp.text, sheet)
    if not report.ok:
        log.warning(
            "the debrief phrasing had numbers outside the facts: using the template",
            extra={"violations": [(v.kind, v.text) for v in report.violations]},
        )
        return local, "local"
    from ..chat.session import decimal_comma, lap_times_as_clock

    return decimal_comma(lap_times_as_clock(resp.text.strip(), lang), lang), "model"
