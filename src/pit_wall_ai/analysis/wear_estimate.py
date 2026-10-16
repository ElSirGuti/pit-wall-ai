"""Tyre wear when the simulator does not show it live (iRacing: only read at a pit stop).

The wear is known at the stops; between them it is an estimate from the laps run since the last reading and the
wear per lap measured on the stints already read for that compound. It is always labelled as an estimate and
carries a range that widens with the laps since the last reading. See ``docs/PHASE8_PLAN.md`` 3.5.

Values are "remaining life in %" per wheel (FL, FR, RL, RR), as the game's tyre apps show them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WHEELS = 4
# the uncertainty of one reading and of a stint that is not like the others, in points of life per lap
MIN_SPREAD_PER_LAP = 0.15


@dataclass(frozen=True)
class Reading:
    compound: str
    laps_on_set: int  # laps the set had run when it was read
    life: tuple[float, float, float, float]  # remaining life in % at that moment


@dataclass(frozen=True)
class Estimate:
    life: tuple[float, float, float, float]
    low: tuple[float, float, float, float]  # pessimistic
    high: tuple[float, float, float, float]  # optimistic
    laps_since_reading: int
    source: str  # "pit" (read, nothing since) | "estimate"

    @property
    def worst_wheel(self) -> int:
        return min(range(WHEELS), key=lambda i: self.life[i])


@dataclass
class WearEstimator:
    readings: list[Reading] = field(default_factory=list)

    def add(self, compound: str, laps_on_set: int, life) -> None:
        """A reading taken at a stop (or from a tyre screen): the set had run ``laps_on_set`` laps."""
        vals = tuple(float(x) for x in life)
        if len(vals) != WHEELS or laps_on_set <= 0:
            return
        self.readings.append(Reading(compound, int(laps_on_set), vals))  # type: ignore[arg-type]

    def _stints(self, compound: str) -> list[Reading]:
        return [r for r in self.readings if r.compound == compound and r.laps_on_set > 0]

    def rates(self, compound: str) -> list[tuple[float, ...]]:
        """Points of life lost per lap, per wheel, on each stint read for this compound."""
        return [
            tuple(max(0.0, (100.0 - v) / r.laps_on_set) for v in r.life)
            for r in self._stints(compound)
        ]

    def rate(self, compound: str) -> tuple[float, ...] | None:
        stints = self._stints(compound)
        if not stints:
            return None
        rs = self.rates(compound)
        total = sum(r.laps_on_set for r in stints)
        return tuple(
            sum(rate[i] * r.laps_on_set for rate, r in zip(rs, stints)) / total
            for i in range(WHEELS)
        )

    def spread(self, compound: str) -> float:
        """How much the stints disagree about the wear per lap (points per lap), never below a floor."""
        rs = self.rates(compound)
        if len(rs) < 2:
            return MIN_SPREAD_PER_LAP
        per_wheel = []
        for i in range(WHEELS):
            vals = [r[i] for r in rs]
            per_wheel.append((max(vals) - min(vals)) / 2)
        return max(MIN_SPREAD_PER_LAP, max(per_wheel))

    def estimate(
        self,
        compound: str,
        laps_on_set: int,
        *,
        last_reading: tuple[float, float, float, float] | None = None,
        laps_at_reading: int = 0,
    ) -> Estimate | None:
        """The life now: from the last reading minus the wear of the laps since, or from a fresh set."""
        rate = self.rate(compound)
        if rate is None and last_reading is None:
            return None
        base = last_reading if last_reading is not None else (100.0,) * WHEELS
        since = max(0, laps_on_set - (laps_at_reading if last_reading is not None else 0))
        if since == 0 and last_reading is not None:
            return Estimate(tuple(base), tuple(base), tuple(base), 0, "pit")  # type: ignore[arg-type]
        if rate is None:
            return None
        spread = self.spread(compound)
        life = tuple(max(0.0, b - r * since) for b, r in zip(base, rate))
        low = tuple(max(0.0, b - (r + spread) * since) for b, r in zip(base, rate))
        high = tuple(min(100.0, b - max(0.0, r - spread) * since) for b, r in zip(base, rate))
        return Estimate(life, low, high, since, "estimate")  # type: ignore[arg-type]

    def to_dict(self) -> dict:
        return {
            "readings": [
                {"compound": r.compound, "laps": r.laps_on_set, "life": list(r.life)}
                for r in self.readings
            ]
        }

    @classmethod
    def from_dict(cls, d: dict) -> WearEstimator:
        est = cls()
        for r in d.get("readings", []):
            est.add(str(r["compound"]), int(r["laps"]), r["life"])
        return est


def wear_text(lang: str, est: Estimate, wheel_names: tuple[str, str, str, str]) -> str:
    """How the engineer says it: the number with its nature (read at the stop, or an estimate with a range)."""
    i = est.worst_wheel
    now = round(est.life[i])
    name = wheel_names[i]
    if est.source == "pit":
        return {
            "es": f"En la última parada el {name} estaba al {now} %.",
            "en": f"At the last stop the {name} was at {now} %.",
            "de": f"Beim letzten Stopp war der {name} bei {now} %.",
        }.get(lang, f"At the last stop the {name} was at {now} %.")
    lo, hi = round(est.low[i]), round(est.high[i])
    n = est.laps_since_reading
    return {
        "es": f"Estimo el {name} en un {now} %, entre {lo} y {hi} %: es una estimación, van {n} vueltas desde la última lectura.",
        "en": f"I estimate the {name} at {now} %, between {lo} and {hi} %: an estimate, {n} laps since the last reading.",
        "de": f"Ich schätze den {name} auf {now} %, zwischen {lo} und {hi} %: eine Schätzung, {n} Runden seit der letzten Messung.",
    }.get(lang, "")
