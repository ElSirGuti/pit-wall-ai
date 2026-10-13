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
        "fuel_warn": "Gasolina para {laps} {laps_word}, ahorra",
        "fuel_critical": "Gasolina para {laps} {laps_word}, box",
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
        "fuel_warn": "Fuel for {laps} {laps_word}, save",
        "fuel_critical": "Fuel for {laps} {laps_word}, box",
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


def laps_word(lang: str, n: int) -> str:
    return {"es": ("vuelta", "vueltas"), "en": ("lap", "laps")}[lang][0 if n == 1 else 1]


def lap_time_words(ms: int) -> str:
    """1:24.913 -> "1 24 9": minutes, seconds, tenths (what a race engineer reads out)."""
    total = max(0, int(ms))
    m, rest = divmod(total, 60_000)
    s, milli = divmod(rest, 1000)
    return f"{m} {s} {milli // 100}"
