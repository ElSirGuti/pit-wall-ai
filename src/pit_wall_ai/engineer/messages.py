"""Spoken messages: short, fixed shape "fact + action", in Spanish, English and German.

Each one must be understood at 200 km/h in under two seconds, so none has more than ~6 words. Numbers are
written the way the voice should read them.
"""

from __future__ import annotations

_WHEEL = {
    "es": {
        "FL": "delantero izquierdo",
        "FR": "delantero derecho",
        "RL": "trasero izquierdo",
        "RR": "trasero derecho",
    },
    "en": {"FL": "front left", "FR": "front right", "RL": "rear left", "RR": "rear right"},
    "de": {"FL": "vorne links", "FR": "vorne rechts", "RL": "hinten links", "RR": "hinten rechts"},
}
_AXLE = {
    "es": {"front": "delanteros", "rear": "traseros"},
    "en": {"front": "front", "rear": "rear"},
    "de": {"front": "vorne", "rear": "hinten"},
}

TEXTS: dict[str, dict[str, str]] = {
    "es": {
        "lockup": "Bloqueo {where}, suelta freno",
        "wheelspin": "Patinaje {axle}, suaviza gas",
        "spin": "Trompo, embrague y freno",
        "offtrack": "Fuera de pista, vuelve despacio",
        "fuel_warn": "Gasolina para {amount}, ahorra",
        "fuel_critical": "Gasolina para {amount}, box",
        "tyre_hot": "{axle} calientes, levanta",
        "pressure_high": "Presión alta {where}, ajusta",
        "puncture": "Pinchazo {where}, box",
        "brake_hot": "Frenos calientes, frena antes",
        "penalty": "Penalización de {seconds} segundos, cumple",
        "flag_yellow": "Bandera amarilla, levanta",
        "flag_blue": "Bandera azul, cede",
        "flag_black": "Bandera negra, box",
        "flag_white": "Bandera blanca, última vuelta",
        "flag_blue_by": "Bandera azul, deja pasar al {name}",
        "flag_return": "Bandera de penalización, entra a boxes",
        "flag_finish": "Bandera a cuadros, termina la vuelta",
        "rival_behind": "El de atrás está a {gap}, se acerca",
        "rival_ahead": "Tienes al de delante a {gap}, ataca",
        "rival_pit": "El de delante entra a boxes",
        "damage": "Daño detectado, revisa",
        "lap": "Vuelta {n}, {time}, {delta}",
        "lap_best": "Mejor vuelta, {time}",
        "delta_faster": "menos {tenths} décimas",
        "delta_slower": "más {tenths} décimas",
        "delta_slower_s": "más {seconds} segundos",
        "delta_same": "mismo ritmo",
        "best_sector": "Mejor sector {n}",
        "test": "Prueba de voz, ingeniero listo",
    },
    "en": {
        "lockup": "{where} lock-up, ease the brake",
        "wheelspin": "{axle} wheelspin, smooth throttle",
        "spin": "Spin, clutch and brake",
        "offtrack": "Off track, rejoin carefully",
        "fuel_warn": "Fuel for {amount}, save",
        "fuel_critical": "Fuel for {amount}, box",
        "tyre_hot": "{axle} tyres hot, lift",
        "pressure_high": "High pressure {where}, adjust",
        "puncture": "Puncture {where}, box",
        "brake_hot": "Brakes hot, brake earlier",
        "penalty": "{seconds} second penalty, serve it",
        "flag_yellow": "Yellow flag, lift",
        "flag_blue": "Blue flag, let him by",
        "flag_black": "Black flag, box",
        "flag_white": "White flag, last lap",
        "flag_blue_by": "Blue flag, let {name} by",
        "flag_return": "Penalty flag, come into the pits",
        "flag_finish": "Chequered flag, finish the lap",
        "rival_behind": "The car behind is {gap} back and closing",
        "rival_ahead": "The car ahead is {gap} away, go get him",
        "rival_pit": "The car ahead is coming into the pits",
        "damage": "Damage detected, check",
        "lap": "Lap {n}, {time}, {delta}",
        "lap_best": "Best lap, {time}",
        "delta_faster": "{tenths} tenths up",
        "delta_slower": "{tenths} tenths down",
        "delta_slower_s": "{seconds} seconds down",
        "delta_same": "same pace",
        "best_sector": "Best sector {n}",
        "test": "Voice test, engineer ready",
    },
    "de": {
        "lockup": "{where} blockiert, Bremse lösen",
        "wheelspin": "Durchdrehen {axle}, Gas dosieren",
        "spin": "Dreher, Kupplung und Bremse",
        "offtrack": "Neben der Strecke, vorsichtig zurück",
        "fuel_warn": "Sprit für {amount}, sparen",
        "fuel_critical": "Sprit für {amount}, Box",
        "tyre_hot": "Reifen {axle} heiß, Fuß vom Gas",
        "pressure_high": "Reifendruck hoch {where}, anpassen",
        "puncture": "Reifenschaden {where}, Box",
        "brake_hot": "Bremsen heiß, früher bremsen",
        "penalty": "{seconds} Sekunden Strafe, abbüßen",
        "flag_yellow": "Gelbe Flagge, Fuß vom Gas",
        "flag_blue": "Blaue Flagge, lass ihn vorbei",
        "flag_black": "Schwarze Flagge, Box",
        "flag_white": "Weiße Flagge, letzte Runde",
        "flag_blue_by": "Blaue Flagge, lass {name} vorbei",
        "flag_return": "Strafflagge, komm an die Box",
        "flag_finish": "Zielflagge, fahr die Runde zu Ende",
        "rival_behind": "Der Hintermann ist {gap} dran und kommt näher",
        "rival_ahead": "Der Vordermann ist {gap} weg, hol ihn dir",
        "rival_pit": "Der Vordermann fährt in die Box",
        "damage": "Schaden erkannt, prüfen",
        "lap": "Runde {n}, {time}, {delta}",
        "lap_best": "Schnellste Runde, {time}",
        "delta_faster": "{tenths} schneller",
        "delta_slower": "{tenths} langsamer",
        "delta_slower_s": "{seconds} Sekunden langsamer",
        "delta_same": "gleiches Tempo",
        "best_sector": "Bestzeit Sektor {n}",
        "test": "Sprachtest, Ingenieur bereit",
    },
}


def wheel_name(lang: str, wheel: str) -> str:
    return _WHEEL[lang][wheel]


def wheels_name(lang: str, wheels: list[str]) -> str:
    """ "front left" / "fronts" / "rears" / "several wheels"."""
    s = set(wheels)
    if len(s) == 1:
        return _WHEEL[lang][next(iter(s))]
    if s == {"FL", "FR"}:
        return {"es": "delanteros", "en": "front axle", "de": "Vorderachse"}[lang]
    if s == {"RL", "RR"}:
        return {"es": "traseros", "en": "rear axle", "de": "Hinterachse"}[lang]
    return {"es": "varias ruedas", "en": "several wheels", "de": "mehrere Räder"}[lang]


def axle_name(lang: str, axle: str) -> str:
    return _AXLE[lang][axle]


def say(lang: str, key: str, **values) -> str:
    return TEXTS[lang][key].format(**values)


_NUM = {
    "es": [
        "cero",
        "una",
        "dos",
        "tres",
        "cuatro",
        "cinco",
        "seis",
        "siete",
        "ocho",
        "nueve",
        "diez",
    ],
    "en": ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"],
    "de": [
        "null",
        "eins",
        "zwei",
        "drei",
        "vier",
        "fünf",
        "sechs",
        "sieben",
        "acht",
        "neun",
        "zehn",
    ],
}


def gap_phrase(lang: str, seconds: float) -> str:
    """A gap to another car as it is said: "8 décimas", "1 coma 3 segundos"."""
    return _tenths(lang, abs(seconds))


def laps_phrase(lang: str, remaining: float) -> str:
    """Laps of fuel as a person says it: "casi dos vueltas", "vuelta y media", "3 vueltas".

    Half-lap bins, never more generous than the number: 1.9 is "almost two", 1.5 is "a lap and a half".
    """
    n, frac = int(remaining), remaining - int(remaining)
    num = lambda k: _NUM[lang][k] if k < len(_NUM[lang]) else str(k)  # noqa: E731
    if lang == "de":
        if frac >= 0.75:
            k = n + 1
            return f"fast {num(k)} Runden" if k > 1 else "fast eine Runde"
        if frac >= 0.5:
            if n == 0:
                return "eine halbe Runde"
            return "anderthalb Runden" if n == 1 else f"{num(n)}einhalb Runden"
        if n == 0:
            return "weniger als eine halbe Runde"
        return "eine Runde" if n == 1 else f"{num(n)} Runden"
    es = lang == "es"
    if frac >= 0.75:  # almost the next whole number
        k = n + 1
        if es:
            return f"casi {num(k)} vueltas" if k > 1 else "casi una vuelta"
        return f"almost {num(k)} laps" if k > 1 else "almost a lap"
    if frac >= 0.5:  # and a half
        if n == 0:
            return "media vuelta" if es else "half a lap"
        if n == 1:
            return "vuelta y media" if es else "a lap and a half"
        return f"{num(n)} vueltas y media" if es else f"{num(n)} and a half laps"
    if n == 0:
        return "menos de media vuelta" if es else "less than half a lap"
    if n == 1:
        return "una vuelta" if es else "one lap"
    return f"{num(n)} vueltas" if es else f"{num(n)} laps"


def lap_time_words(ms: int) -> str:
    """1:24.913 -> "1 24 9": minutes, seconds, tenths (what a race engineer reads out)."""
    total = max(0, int(ms))
    m, rest = divmod(total, 60_000)
    s, milli = divmod(rest, 1000)
    return f"{m} {s} {milli // 100}"


def _tenths(lang: str, seconds: float) -> str:
    """A time loss as it is said: "2 décimas" below a second, "1,3 segundos" above."""
    if lang == "de":
        if seconds < 0.95:
            return f"{max(1, round(seconds * 10))} Zehntel"
        return f"{seconds:.1f}".replace(".", " Komma ") + " Sekunden"
    if seconds < 0.95:
        n = max(1, round(seconds * 10))
        if lang == "es":
            return f"{n} décima" if n == 1 else f"{n} décimas"
        return f"{n} tenth" if n == 1 else f"{n} tenths"
    value = f"{seconds:.1f}"
    return value.replace(".", " coma ") + " segundos" if lang == "es" else f"{value} seconds"


def where_phrase(
    lang: str, *, name: str | None, number: int | None, direction: str, at_m: float
) -> str:
    """How the corner is referred to: its name, its number (only if verified) or the stretch."""
    if name:
        return name
    if number is not None:
        return {"es": f"Curva {number}", "en": f"Corner {number}", "de": f"Kurve {number}"}[lang]
    d = round(at_m / 10.0) * 10
    if lang == "de":
        side = "Rechtskurve" if direction == "right" else "Linkskurve"
        return f"{side} {d} Meter nach der Ziellinie"
    if lang == "es":
        side = "derechas" if direction == "right" else "izquierdas"
        return f"Curva de {side} a {d} metros de meta"
    return f"{direction.capitalize()} corner at {d} meters from the line"


def braking_hint(lang: str, *, where: str, metres: float, earlier: bool, loss_s: float) -> str:
    """The measured fact first (braking point against the best lap), then the time it cost. No causes."""
    m = round(abs(metres))
    if lang == "de":
        how = "früher" if earlier else "später"
        return f"{where}: du bremst {m} Meter {how} als in deiner schnellsten Runde, {_tenths(lang, loss_s)} verloren"
    if lang == "es":
        how = "antes" if earlier else "más tarde"
        return (
            f"{where}: frenas {m} metros {how} que tu mejor vuelta, pierdes {_tenths(lang, loss_s)}"
        )
    how = "earlier" if earlier else "later"
    return f"{where}: you brake {m} meters {how} than your best lap, losing {_tenths(lang, loss_s)}"


def loss_hint(lang: str, *, where: str, loss_s: float, min_speed_delta: float | None) -> str:
    """A big time loss in a corner without a braking point to compare: time, and the slowest speed if it was
    clearly lower. Facts only."""
    lost = _tenths(lang, loss_s)
    slower = min_speed_delta is not None and min_speed_delta <= -5.0
    if lang == "de":
        base = f"{where}: {lost} verloren"
        return base + (
            f", Mindestgeschwindigkeit {round(-min_speed_delta)} Kilometer pro Stunde niedriger"
            if slower
            else ""
        )
    if lang == "es":
        base = f"{where}: pierdes {lost}"
        return base + (
            f", velocidad mínima {round(-min_speed_delta)} kilómetros por hora menor"
            if slower
            else ""
        )
    base = f"{where}: losing {lost}"
    return base + (f", minimum speed {round(-min_speed_delta)} km/h lower" if slower else "")
