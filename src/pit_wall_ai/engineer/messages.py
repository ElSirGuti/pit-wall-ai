"""Spoken messages: short, fixed shape "fact + action", in Spanish and English.

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
}
_AXLE = {
    "es": {"front": "delanteros", "rear": "traseros"},
    "en": {"front": "front", "rear": "rear"},
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
}


def wheel_name(lang: str, wheel: str) -> str:
    return _WHEEL[lang][wheel]


def wheels_name(lang: str, wheels: list[str]) -> str:
    """ "front left" / "fronts" / "rears" / "several wheels"."""
    s = set(wheels)
    if len(s) == 1:
        return _WHEEL[lang][next(iter(s))]
    if s == {"FL", "FR"}:
        return {"es": "delanteros", "en": "front axle"}[lang]
    if s == {"RL", "RR"}:
        return {"es": "traseros", "en": "rear axle"}[lang]
    return {"es": "varias ruedas", "en": "several wheels"}[lang]


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
}


def laps_phrase(lang: str, remaining: float) -> str:
    """Laps of fuel as a person says it: "casi dos vueltas", "vuelta y media", "3 vueltas".

    Half-lap bins, never more generous than the number: 1.9 is "almost two", 1.5 is "a lap and a half".
    """
    n, frac = int(remaining), remaining - int(remaining)
    num = lambda k: _NUM[lang][k] if k < len(_NUM[lang]) else str(k)  # noqa: E731
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
        return f"Curva {number}" if lang == "es" else f"Corner {number}"
    d = round(at_m / 10.0) * 10
    if lang == "es":
        side = "derechas" if direction == "right" else "izquierdas"
        return f"Curva de {side} a {d} metros de meta"
    return f"{direction.capitalize()} corner at {d} meters from the line"


def braking_hint(lang: str, *, where: str, metres: float, earlier: bool, loss_s: float) -> str:
    """The measured fact first (braking point against the best lap), then the time it cost. No causes."""
    m = round(abs(metres))
    if lang == "es":
        how = "antes" if earlier else "más tarde"
        return (
            f"{where}: frenas {m} metros {how} que tu mejor vuelta, pierdes {_tenths(lang, loss_s)}"
        )
    how = "earlier" if earlier else "later"
    return f"{where}: you brake {m} meters {how} than your best lap, losing {_tenths(lang, loss_s)}"
