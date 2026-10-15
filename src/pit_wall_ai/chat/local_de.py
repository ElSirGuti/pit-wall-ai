"""German local answers: the keyword tables and the answer builders of ``local.py`` for ``de``.

Same rules as the Spanish and English ones: the router is deterministic keyword matching on the normalised
question (accents folded, ``ß`` written ``ss``), and every answer is written from the **same fact sheet** the
model receives, with the fact keys listed so the grounding check can verify it.

The tables are keyed by the *value* of the intent (``"fuel"``, ``"tyres"``...) so this module needs nothing
from ``local.py`` and the two do not import each other at load time.
"""

from __future__ import annotations

import re

NO_DATA = "Diese Angabe habe ich nicht."

# keyword tables, written already folded (ä -> a, ö -> o, ü -> u, ß -> ss)
KEYS: dict[str, tuple[str, ...]] = {
    "last_alert": (
        "was war das",
        "warum hast du gewarnt",
        "warum hast du mich gewarnt",
        "warum hast du das gesagt",
        "letzte warnung",
        "letzte meldung",
        "letzter hinweis",
    ),
    "loss": (
        "verliere",
        "verloren",
        "verlust",
        "zeit verliere",
        "bremspunkt",
        "bremspunkte",
        "bremspunkten",
        "wie war das bremsen",
        "wo kann ich mich verbessern",
        "wo verbessern",
    ),
    "penalty": ("strafe", "strafen", "zeitstrafe", "penalty", "durchfahrtsstrafe"),
    "fuel": (
        "sprit",
        "benzin",
        "kraftstoff",
        "tank",
        "verbrauch",
        "liter",
        "wie viel sprit",
        "reicht der sprit",
    ),
    "tyres": (
        "reifen",
        "reifendruck",
        "reifentemperatur",
        "druck",
        "psi",
        "heissesten",
        "heisseste",
        "waermsten",
        "warmsten",
        "kaltesten",
        "kalteste",
    ),
    "brakes": ("bremse", "bremsen", "bremstemperatur", "bremsscheiben"),
    "laps": (
        "schnellste runde",
        "beste runde",
        "letzte runde",
        "bestzeit",
        "rundenzeit",
        "rundenzeiten",
        "delta",
        "meine zeiten",
        "wie schnell war ich",
    ),
    "session": (
        "welche session",
        "welche sitzung",
        "welcher platz",
        "welche position",
        "meine position",
        "in welcher position",
        "runden gefahren",
        "wie viele runden bin ich",
    ),
    "tyre_corners": (
        "in welchen kurven verschleisse",
        "in welchen kurven verbrauche",
        "welche kurven verschleissen",
        "wo verschleisse ich",
        "wo verbrauche ich die reifen",
        "wo verbrauche ich am meisten reifen",
        "in welchen kurven gehen die reifen",
    ),
    "time_left": (
        "wie viel zeit",
        "wie lange noch",
        "restzeit",
        "verbleibende zeit",
        "wann endet",
        "wie lange dauert",
    ),
    "not_yet": (
        "setup",
        "abstimmung",
        "strategie",
        "rennstrategie",
        "boxenstopp",
        "wann boxen",
        "wann soll ich",
        "wann muss ich",
    ),
    "unknown_data": (
        "wetter",
        "regen",
        "lufttemperatur",
        "streckentemperatur",
        "gegner",
        "rivalen",
        "andere fahrer",
        "wer ist vor mir",
        "wer ist hinter mir",
        "oltemperatur",
        "wassertemperatur",
    ),
}
IDENTITY = (
    "welche kurve",
    "was ist",
    "welche ist",
    "wo ist",
    "welche",
)
FORCE_MODEL = (
    "zusammenfassung",
    "fasse zusammen",
    "fass zusammen",
    "erklare",
    "erklar",
    "meinung",
    "wie bin ich gefahren",
)
HOTTEST = ("heissesten", "heisseste", "waermsten", "warmsten")
COLDEST = ("kaltesten", "kalteste")

_KIND = {"practice": "Training", "qualify": "Qualifying", "race": "Rennen", "hotlap": "Hotlap"}
_WHEEL = {"FL": "vorne links", "FR": "vorne rechts", "RL": "hinten links", "RR": "hinten rechts"}
_SEVERITY = {"slow": "langsame", "medium": "mittelschnelle", "fast": "schnelle"}


def fold(question: str) -> str:
    """``ß`` is not removed by the accent folding of ``norm``: write it ``ss`` first."""
    return question.replace("ß", "ss").replace("ẞ", "SS")


def num(v: float, nd: int) -> str:
    return f"{v:.{nd}f}".replace(".", ",")


def clock(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}".replace(".", ",")


def corner_ref(sheet, number: int, names: dict[int, str]) -> str:
    name = names.get(number)
    base = f"Kurve {number}"
    return f"{name} ({base})" if name and not re.fullmatch(r"(?i)curva\s+\d+", name) else base


# ── builders: (sheet, track, routed, thresholds) -> (text, fact keys) ─────────────────────────────────────
def fuel(sheet, track, routed, th):
    litres = sheet.value("fuel.litres")
    if litres is None:
        return NO_DATA + " (Sprit)", []
    per, left = sheet.value("fuel.per_lap_l"), sheet.value("fuel.laps_left")
    if per is None or left is None:
        return (
            f"Noch {num(litres, 1)} Liter im Tank; den Verbrauch pro Runde habe ich noch nicht "
            "(dafür brauche ich vollständige gezeitete Runden)."
        ), ["fuel.litres"]
    from ..engineer import messages as M

    phrase = M.laps_phrase("de", float(left))
    return (
        f"Noch {num(litres, 1)} Liter; du verbrauchst {num(per, 2)} Liter pro Runde, "
        f"das sind {phrase} ({num(left, 1)} Runden)."
    ), ["fuel.litres", "fuel.per_lap_l", "fuel.laps_left"]


def laps(sheet, track, routed, th):
    keys: list[str] = []
    out: list[str] = []
    best, bl = sheet.value("laps.best.time_s"), sheet.value("laps.best.lap")
    last, ll = sheet.value("laps.last.time_s"), sheet.value("laps.last.lap")
    if last is None and best is None:
        return "Es gibt noch keine gezeiteten Runden.", []
    if last is not None:
        keys += ["laps.last.time_s", "laps.last.lap"]
        flags = sheet.value("laps.last.flags")
        extra = f" ({flags})" if flags else ""
        out.append(f"Letzte Runde, Nummer {ll}: {clock(last)}{extra}.")
    if best is not None:
        keys += ["laps.best.time_s", "laps.best.lap"]
        out.append(f"Schnellste Runde, Nummer {bl}: {clock(best)}.")
    if ll is not None and ll == bl and last is not None:
        out.append("Die letzte Runde ist deine schnellste.")
    d = sheet.value("laps.last.delta_to_best_s")
    if d is not None:
        keys.append("laps.last.delta_to_best_s")
        word = "langsamer" if d > 0 else "schneller"
        out.append(f"Die letzte war {num(abs(d), 3)} s {word} als die schnellste.")
    live = sheet.value("laps.current.live_delta_s")
    if live is not None:
        keys.append("laps.current.live_delta_s")
        out.append(f"Live-Delta: {f'{live:+.2f}'.replace('.', ',')} s gegenüber der Referenz.")
    return " ".join(out), keys


def tyres(sheet, track, routed, th):
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
            bits.append(f"{num(core, 0)} °C")
            keys.append(f"tyres.{w}.core_c")
        if p is not None:
            bits.append(f"{num(p, 1)} psi")
            keys.append(f"tyres.{w}.pressure_psi")
        if wear is not None:
            bits.append(f"Verschleiß {num(wear, 1)} %")
            keys.append(f"tyres.{w}.wear_pct")
        rows.append(f"{_WHEEL[w]}: {', '.join(bits)}")
    if not rows:
        return NO_DATA, []
    temps = {w: sheet.value(f"tyres.{w}.core_c") for w in _WHEEL}
    temps = {w: t for w, t in temps.items() if t is not None}
    extra = []
    if temps and "hottest" in routed.flags:
        w = max(temps, key=temps.get)
        extra.append(f"Am heißesten ist der Reifen {_WHEEL[w]} mit {num(temps[w], 0)} °C.")
    if temps and "coldest" in routed.flags:
        w = min(temps, key=temps.get)
        extra.append(f"Am kältesten ist der Reifen {_WHEEL[w]} mit {num(temps[w], 0)} °C.")
    comp = sheet.value("session.compound")
    head = f"Reifen ({comp}): " if comp else "Reifen: "
    return " ".join([*extra, head + "; ".join(rows) + "."]), keys


def brakes(sheet, track, routed, th):
    rows, keys = [], []
    for w in _WHEEL:
        t = sheet.value(f"brakes.{w}.temp_c")
        if t is not None:
            rows.append(f"{_WHEEL[w]}: {num(t, 0)} °C")
            keys.append(f"brakes.{w}.temp_c")
    if rows:
        return "Bremsen: " + "; ".join(rows) + ".", keys
    return (
        "Die Bremstemperatur ist bei diesem Auto nicht verlässlich: der Sensor bewegt sich nicht, "
        "deshalb nutze ich sie nicht."
    ), ["brakes.note"]


def session(sheet, track, routed, th):
    kind = sheet.value("session.kind")
    if kind is None:
        return NO_DATA, []
    keys = ["session.kind"]
    bits = [_KIND.get(kind, kind)]
    phase = sheet.value("session.phase")
    if phase and phase not in ("flying",):
        bits.append(phase)
    out = f"Session: {', '.join(bits)}."
    left = sheet.value("session.time_left_s")
    if left is not None and left > 0:
        keys.append("session.time_left_s")
        mm, ss = divmod(int(left), 60)
        out += f" Noch {mm}:{ss:02d}."
    pos = sheet.value("session.position")
    if pos is not None:
        keys.append("session.position")
        out += f" Position {pos}."
    n = sheet.value("session.laps_completed")
    if n is not None:
        keys.append("session.laps_completed")
        out += f" Gefahrene Runden: {n}."
    return out, keys


def penalty(sheet, track, routed, th):
    t = sheet.value("penalties.time_s")
    if t is None:
        return "Ich habe keine aktiven Strafen.", []
    return (
        f"Das Spiel meldet {num(t, 1)} s Strafe (die Einheit dieses Feldes ist noch nicht verifiziert)."
    ), ["penalties.time_s"]


_WHY = (
    (
        "no Assetto Corsa tracks folder",
        "ich finde den Strecken-Ordner von Assetto Corsa auf diesem Rechner nicht (analysis.ac_dirs)",
    ),
    (
        "no fast_lane.ai for track",
        "für diese Strecke gibt es keine Ideallinie in der Spielinstallation",
    ),
    (
        "no valid reference lap",
        "es gibt noch keine gültige Referenzrunde mit demselben Reifentyp",
    ),
    ("no line of", "keine Ideallinie passt zur Streckenlänge (anderes Layout?)"),
    ("no game length", "ohne die Länge aus dem Spiel weiß ich nicht, welches Layout es ist"),
)


def _why(reason: str) -> str:
    for prefix, text in _WHY:
        if reason.startswith(prefix) or prefix in reason[:60]:
            return text
    return reason


def loss(sheet, track, routed, th):
    unavailable = sheet.value("analysis.unavailable")
    lap = sheet.value("analysis.lap")
    names = track.names if track is not None else {}
    if lap is None:
        why = _why(str(unavailable or "für diese Strecke gibt es keine Bremsanalyse"))
        return f"Das kann ich nicht sagen: {why}.", ["analysis.unavailable"]
    ref = sheet.value("analysis.reference_lap")
    keys = ["analysis.lap", "analysis.reference_lap"]
    if routed.corners:
        return _loss_of_corner(sheet, names, routed.corners, lap, keys)
    w = sheet.value("analysis.worst.corner")
    if w is None:
        d, s = th
        return (
            f"In Runde {lap} gibt es gegenüber Runde {ref} (deine schnellste) keine Kurve mit einem "
            f"Bremspunkt-Unterschied von mindestens {num(d, 0)} m und einem Verlust von mindestens {num(s, 1)} s."
        ), keys
    where = corner_ref(sheet, int(w), names)
    be = sheet.value("analysis.worst.brake_earlier_m")
    lost = sheet.value("analysis.worst.time_loss_s")
    kind = sheet.value("analysis.worst.kind")
    ms = sheet.value("analysis.worst.min_speed_delta_kmh")
    keys += ["analysis.worst.corner", "analysis.worst.time_loss_s"]
    if kind == "brake" and be is not None:
        keys.append("analysis.worst.brake_earlier_m")
        how = "früher" if be > 0 else "später"
        return (
            f"In Runde {lap}, bei {where}: du verlierst {num(lost, 2)} s und bremst {num(abs(be), 0)} m "
            f"{how} als in deiner schnellsten Runde."
        ), keys
    extra = ""
    if ms is not None and ms <= -5:
        keys.append("analysis.worst.min_speed_delta_kmh")
        extra = f", mit {num(abs(ms), 0)} km/h weniger Mindestgeschwindigkeit"
    return (
        f"In Runde {lap}, bei {where}: du verlierst {num(lost, 2)} s gegenüber deiner schnellsten Runde{extra}."
    ), keys


def _loss_of_corner(sheet, names, corners, lap, keys):
    if len(corners) > 1:
        opts = ", ".join(corner_ref(sheet, n, names) for n in corners)
        return f"Das kann sein: {opts}. Welche?", []
    n = corners[0]
    where = corner_ref(sheet, n, names)
    k = f"analysis.corners.C{n}"
    td = sheet.value(f"{k}.time_diff_s")
    if td is None:
        return (
            f"In Runde {lap} habe ich keinen Vergleich von {where} mit deiner schnellsten Runde: "
            "diese Kurve konnte in einer der beiden nicht gemessen werden."
        ), keys
    keys = [*keys, f"{k}.time_diff_s"]
    be = sheet.value(f"{k}.brake_earlier_m")
    gist = f"du verlierst {num(abs(td), 2)} s" if td >= 0 else f"du gewinnst {num(abs(td), 2)} s"
    if be is not None:
        keys.append(f"{k}.brake_earlier_m")
        how = "früher" if be > 0 else "später"
        return (
            f"In Runde {lap}, bei {where}: {gist} und bremst {num(abs(be), 0)} m {how} als in deiner "
            "schnellsten Runde."
        ), keys
    return f"In Runde {lap}, bei {where}: {gist} gegenüber deiner schnellsten Runde.", keys


def corner(sheet, track, routed, th):
    if track is None or not routed.corners:
        return NO_DATA, []
    names = track.names
    if len(routed.corners) > 1:
        opts = ", ".join(corner_ref(sheet, n, names) for n in routed.corners)
        return f"Das kann sein: {opts}. Welche?", []
    n = routed.corners[0]
    name = names.get(n)
    named = bool(name) and not re.fullmatch(r"(?i)curva\s+\d+", name or "")
    head = f"{name}, das ist Kurve {n}" if named else f"Das ist Kurve {n}"
    g = track.geometry
    if g is not None:
        c = next((c for c in g.apexes if c.number == n), None)
        if c is not None:
            side = "Rechtskurve" if c.direction == "right" else "Linkskurve"
            sev = _SEVERITY.get(c.severity, c.severity)
            return f"{head}: eine {sev} {side}.", []
    return f"{head}.", []


def last_alert(sheet, track, routed, th):
    text = sheet.value("alerts.A0.text")
    if text is None:
        return "Ich habe noch keine Meldung gegeben.", []
    rule = sheet.value("alerts.A0.rule")
    age = sheet.value("alerts.A0.age_s")
    outcome = sheet.value("alerts.A0.outcome")
    keys = ["alerts.A0.rule", "alerts.A0.text"]
    when = ""
    if age is not None:
        keys.append("alerts.A0.age_s")
        when = f"Vor {num(age, 0)} s "
    said = "" if outcome == "said" else f" (nicht gesprochen: {outcome})"
    return f"{when}war die Meldung „{text}“, von der Regel {rule}.{said}", keys


def time_left(sheet, track, routed, th):
    left = sheet.value("session.time_left_s")
    if left is not None and left > 0:
        mm, ss = divmod(int(left), 60)
        return f"Noch {mm}:{ss:02d}.", ["session.time_left_s"]
    if sheet.value("session.timed") is False:
        return (
            "Diese Session hat kein Zeitlimit: es gibt keine Restzeit zu nennen.",
            ["session.timed"],
        )
    return "Das Spiel gibt mir die Restzeit dieser Session nicht.", []


def not_yet(sheet, track, routed, th):
    return (
        "Setup-Ratschläge und Rennstrategie sind noch nicht verfügbar: sie kommen in einer späteren "
        "Phase des Projekts. Jetzt kann ich dir sagen, was das Auto in dieser Session misst."
    ), []


def unknown(sheet, track, routed, th):
    return (
        "Diese Angabe habe ich nicht: ich sehe nur die Telemetrie deines Autos und der Session, "
        "nicht das Wetter oder andere Fahrer."
    ), []


def straight(sheet, track, routed, th):
    from . import local as L

    names = track.names if track is not None else {}
    return L.straight_answer(sheet, "de", track, routed, lambda c: corner_ref(sheet, c, names))


def tyre_corners(sheet, track, routed, th):
    from . import local as L

    names = track.names if track is not None else {}
    return L.tyre_corners_answer(sheet, "de", names, lambda c: corner_ref(sheet, c, names))


BUILDERS = {
    "straight": straight,
    "tyre_corners": tyre_corners,
    "fuel": fuel,
    "laps": laps,
    "tyres": tyres,
    "brakes": brakes,
    "session": session,
    "penalty": penalty,
    "loss": loss,
    "corner": corner,
    "last_alert": last_alert,
    "unknown_data": unknown,
    "not_yet": not_yet,
    "time_left": time_left,
}
