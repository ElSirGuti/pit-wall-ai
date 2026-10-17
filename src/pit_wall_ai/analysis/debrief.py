"""The post-session debrief: what happened, where the time went, what to work on (``docs/PHASE7_PLAN.md``).

Everything here is computed from a recording; no model is involved. Every block carries the size of the sample
it was computed on and is left out when there is too little data to say anything. The numbers go into a facts
dict that the spoken summary (and, later, the model) is checked against.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..config import AppConfig
from ..events import Event, LapCompleted
from ..session.engine import SessionEngine
from ..telemetry.snapshot import Snapshot
from . import braking as B
from .circuit_names import corner_names, official_of
from .laps import LapRecord
from .offline import iter_snapshots
from .tracks import resolve
from .tyre_corners import drop_incident_laps, lap_loads, ranking, validate

MIN_PACE_LAPS = 3  # fewer valid laps than this: no consistency, no trend
MIN_CORNER_LAPS = 3  # a corner needs this many compared laps to be called a loss
SLOW_LAP_RATIO = (
    1.07  # a lap slower than this against the best is not "pace" (incident, traffic, in/out)
)
DAMAGE_MIN_INCREASE = 5.0  # the same threshold as the damage rule
FLAGS = {1: "blue", 2: "yellow", 3: "black", 4: "white", 5: "chequered", 6: "penalty"}


@dataclass
class Debrief:
    facts: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)  # what was left out and why (never silent)

    def block(self, name: str) -> dict | None:
        return self.facts.get(name)


# -- helpers --------------------------------------------------------------------------------------------
def _mode_session(snaps: list[Snapshot]) -> str:
    return Counter(s.session_name for s in snaps).most_common(1)[0][0] if snaps else "unknown"


def _slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0


def _fmt_ms(ms: float) -> str:
    m, s = divmod(ms / 1000.0, 60)
    return f"{int(m)}:{s:06.3f}"


def eligible(laps: list[LapRecord]) -> list[LapRecord]:
    return [lap for lap in laps if lap.eligible_for_best]


# -- blocks ---------------------------------------------------------------------------------------------
class SessionStats:
    """What the debrief needs from the samples, kept as they arrive (live) or fed from a recording (offline).

    Only small aggregates are held, never the samples: a live run keeps one of these for the whole session.
    """

    def __init__(self) -> None:
        self.n = 0
        self.t0: float | None = None
        self.sessions: Counter = Counter()
        self.pos_first = 0
        self.pos_last = 0
        self.wear_first: tuple[float, ...] | None = None
        self.wear_low: list[float] = [100.0] * 4
        self.have_csp_wear = False
        self.temps: dict[str, list[float]] = {"front": [100000.0, -1.0], "rear": [100000.0, -1.0]}
        self.max_penalty = 0.0
        self.penalty_events = 0
        self._penalty_last = 0.0
        self.flags: Counter = Counter()
        self._damage_last: float | None = None
        self.hits: list[dict] = []

    def update(self, snap: Snapshot) -> None:
        self.n += 1
        if self.t0 is None:
            self.t0 = snap.t
        self.sessions[snap.session_name] += 1
        if snap.position > 0:
            if not self.pos_first:
                self.pos_first = snap.position
            self.pos_last = snap.position
        if snap.wear_source == "csp":
            if self.wear_first is None:
                self.wear_first = tuple(snap.tyre_wear)
            self.have_csp_wear = True
            self.wear_low = [min(self.wear_low[i], snap.tyre_wear[i]) for i in range(4)]
        if snap.speed_kmh > 50:
            for axle, idx in (("front", (0, 1)), ("rear", (2, 3))):
                for i in idx:
                    t = snap.tyre_core_temp[i]
                    if t > 0:
                        lo, hi = self.temps[axle]
                        self.temps[axle] = [min(lo, t), max(hi, t)]
        self.max_penalty = max(self.max_penalty, snap.penalty_time)
        if snap.penalty_time - self._penalty_last >= 0.5:  # a rise of the counter is one penalty
            self.penalty_events += 1
        self._penalty_last = snap.penalty_time
        if snap.flags:
            self.flags[snap.flags] += 1
        total = sum(snap.car_damage[:4])
        if self._damage_last is not None and total - self._damage_last >= DAMAGE_MIN_INCREASE:
            self.hits.append(
                {
                    "at_s": round(snap.t - (self.t0 or snap.t)),
                    "increase": round(total - self._damage_last, 1),
                }
            )
        self._damage_last = total

    # -- the blocks ---------------------------------------------------------------------------------
    def kind(self) -> str:
        return self.sessions.most_common(1)[0][0] if self.sessions else "unknown"

    def result(self, laps: list[LapRecord], static) -> dict:
        block = {
            "session": self.kind(),
            "track": static.track,
            "layout": static.track_configuration,
            "car": static.car_model,
            "laps": len(laps),
            "cars": int(getattr(static, "num_cars", 0) or 0),
        }
        if self.pos_first:
            block["position_start"] = self.pos_first
            block["position_end"] = self.pos_last
        return block

    def tyres_basic(self) -> dict:
        out: dict = {}
        if self.have_csp_wear and self.wear_first is not None:
            names = ("FL", "FR", "RL", "RR")
            out["wear_used"] = {
                w: round(self.wear_first[i] - self.wear_low[i], 1) for i, w in enumerate(names)
            }
            out["wear_end"] = {w: round(self.wear_low[i], 1) for i, w in enumerate(names)}
            out["wear_source"] = "game"
        for axle in ("front", "rear"):
            lo, hi = self.temps[axle]
            if hi > 0:
                out[f"temp_{axle}"] = {"min": round(lo), "max": round(hi)}
        return out

    def penalties(self) -> dict | None:
        out: dict = {}
        if self.max_penalty > 0:
            out["max_penalty_s"] = round(self.max_penalty, 1)
            out["penalties"] = self.penalty_events
        seen = sorted({FLAGS[f] for f in self.flags if f in FLAGS and f != 5})
        if seen:
            out["flags"] = seen
        if self.hits:
            out["contacts"] = len(self.hits)
            out["worst_contact"] = max(h["increase"] for h in self.hits)
        return out or None


def _stats(snaps: list[Snapshot]) -> SessionStats:
    st = SessionStats()
    for s in snaps:
        st.update(s)
    return st


def result_block(snaps: list[Snapshot], laps: list[LapRecord], static) -> dict:
    return _stats(snaps).result(laps, static)


def pace_block(laps: list[LapRecord]) -> dict | None:
    good = eligible(laps)
    if not good:
        return None
    best = min(good, key=lambda lap: lap.time_ms)
    out: dict = {"best_ms": best.time_ms, "best_lap": best.number, "valid_laps": len(good)}
    sectors = [
        lap.sectors_ms for lap in good if lap.sectors_ms and all(x > 0 for x in lap.sectors_ms)
    ]
    if sectors and len({len(x) for x in sectors}) == 1 and len(sectors) >= 2:
        ideal = sum(min(s[i] for s in sectors) for i in range(len(sectors[0])))
        out["ideal_ms"] = ideal
        out["ideal_gap_ms"] = best.time_ms - ideal
    paced = [lap for lap in good if lap.time_ms <= best.time_ms * SLOW_LAP_RATIO]
    if len(paced) >= MIN_PACE_LAPS:
        times = [lap.time_ms for lap in paced]
        out["consistency_ms"] = round(statistics.pstdev(times))
        out["consistency_laps"] = len(paced)
        out["spread_ms"] = max(times) - min(times)
    return out


def stints_block(laps: list[LapRecord]) -> dict | None:
    if not laps:
        return None
    stints: list[dict] = []
    for lap in laps:
        if not stints or stints[-1]["compound"] != lap.compound:
            stints.append({"compound": lap.compound, "laps": 0, "numbers": []})
        stints[-1]["laps"] += 1
        stints[-1]["numbers"].append(lap.number)
    good = eligible(laps)
    best = min((x.time_ms for x in good), default=0)
    for st in stints:
        # the pace trend leaves out the slow laps (a standing start, traffic): they say nothing about the tyres
        lp = [
            x
            for x in good
            if x.number in st["numbers"] and best and x.time_ms <= best * SLOW_LAP_RATIO
        ]
        if len(lp) >= MIN_PACE_LAPS:
            st["trend_s_per_lap"] = round(
                _slope([float(x.number) for x in lp], [x.time_ms / 1000.0 for x in lp]), 3
            )
            st["trend_laps"] = len(lp)
        st.pop("numbers")
    used = [lap.fuel_used for lap in laps if lap.fuel_used > 0 and lap.eligible_for_best]
    out: dict = {"stints": stints}
    if used:
        out["fuel_per_lap"] = round(statistics.mean(used), 2)
        out["fuel_laps"] = len(used)
    return out


def time_loss_block(
    laps: list[LapRecord], geometry, cfg: AppConfig, names: dict[int, str]
) -> dict | None:
    """The corners that cost the most time against the best lap, averaged over the laps compared."""
    if geometry is None:
        return None
    sums: dict[int, list[B.CornerComparison]] = {}
    for lap in laps:
        if not B.comparable(lap):
            continue
        ref = B.reference_lap(laps, lap)
        if ref is None or not B.representative(lap, ref):
            continue
        for c in B.compare_lap(geometry, lap, ref, cfg.analysis.braking):
            if c.time_diff_s is not None:
                sums.setdefault(c.corner, []).append(c)
    rows = []
    for corner, comps in sums.items():
        if len(comps) < MIN_CORNER_LAPS:
            continue
        mean_loss = statistics.mean(c.time_diff_s for c in comps)  # type: ignore[arg-type]
        if mean_loss <= 0:
            continue
        brakes = [c.brake_earlier_m for c in comps if c.brake_earlier_m is not None]
        rows.append(
            {
                "corner": official_of(geometry, corner),
                "apex": corner,
                "name": names.get(corner, ""),
                "mean_loss_s": round(mean_loss, 2),
                "laps": len(comps),
                "brake_earlier_m": round(statistics.mean(brakes), 1) if len(brakes) >= 2 else None,
            }
        )
    rows.sort(key=lambda r: r["mean_loss_s"], reverse=True)
    return {"corners": rows[:3]} if rows else None


def corner_index_block(snaps: list[Snapshot], geometry, names: dict[int, str]) -> dict | None:
    """Where the tyres work hardest, from the samples of the session (an estimate, labelled as such)."""
    if geometry is None:
        return None
    laps_l = lap_loads(snaps, geometry.apexes, geometry.length_m)
    laps_l, _ = drop_incident_laps(laps_l)
    if len(laps_l) < 3:
        return None
    v = validate(laps_l)
    out: dict = {"validated": bool(v.ok), "laps": len(laps_l), "estimate": True}
    for axle in ("front", "rear"):
        out[axle] = [
            {
                "corner": official_of(geometry, n),
                "apex": n,
                "name": names.get(n, ""),
                "share_pct": round(share * 100, 1),
                "phase": phase,
            }
            for n, share, phase in ranking(laps_l, axle)[:2]
        ]
    return out


def tyres_block(snaps: list[Snapshot], geometry, names: dict[int, str]) -> dict | None:
    out = _stats(snaps).tyres_basic()
    ci = corner_index_block(snaps, geometry, names)
    if ci:
        out["corner_index"] = ci
    return out or None


def incidents_block(laps: list[LapRecord]) -> dict | None:
    odd = [lap.number for lap in laps if lap.suspect or lap.tyres_out_max >= 3]
    return {"laps_with_incidents": odd, "laps": len(laps)} if odd else None


def penalties_block(snaps: list[Snapshot]) -> dict | None:
    return _stats(snaps).penalties()


# -- the whole thing ------------------------------------------------------------------------------------
def build_from_parts(
    stats: SessionStats,
    laps: list[LapRecord],
    static,
    geometry,
    cfg: AppConfig,
    *,
    corner_index: dict | None = None,
) -> Debrief:
    """The debrief from what a run kept (``SessionStats``, the laps, the racing line): live or offline."""
    d = Debrief()
    names = corner_names(geometry, static.track) if geometry is not None else {}
    if geometry is None:
        d.notes.append("time_loss")
    d.facts["result"] = stats.result(laps, static)
    tyres = stats.tyres_basic()
    if corner_index:
        tyres["corner_index"] = corner_index
    for name, block in (
        ("pace", pace_block(laps)),
        ("stints", stints_block(laps)),
        ("time_loss", time_loss_block(laps, geometry, cfg, names)),
        ("tyres", tyres or None),
        ("incidents", incidents_block(laps)),
        ("penalties", stats.penalties()),
    ):
        if block:
            d.facts[name] = block
        elif name in ("pace", "time_loss", "tyres") and name not in d.notes:
            d.notes.append(name)  # nothing on incidents or penalties is not a gap
    return d


def build_debrief(
    path: str | Path, cfg: AppConfig, *, session: str = "practice", ac_dirs=()
) -> Debrief:
    static, gen = iter_snapshots(path, session=session)
    snaps = list(gen)
    d = Debrief()
    if not snaps:
        d.notes.append("pace")
        return d
    engine = SessionEngine()
    events: list[Event] = []
    for s in snaps:
        events += engine.process(s)
    events += engine.flush()
    laps = [e.lap for e in events if isinstance(e, LapCompleted)]
    res = resolve(
        static.track,
        static.track_configuration,
        length_m=static.track_length_m or None,
        configured=[*ac_dirs, *cfg.analysis.ac_dirs],
    )
    geometry = res.geometry if res.ok else None
    names = corner_names(geometry, static.track) if geometry is not None else {}
    return build_from_parts(
        _stats(snaps),
        laps,
        static,
        geometry,
        cfg,
        corner_index=corner_index_block(snaps, geometry, names),
    )


# -- the Markdown report --------------------------------------------------------------------------------
_T = {
    "es": {
        "title": "Resumen de la sesión",
        "result": "Resultado",
        "pace": "Ritmo",
        "stints": "Tandas y combustible",
        "loss": "Dónde se fue el tiempo",
        "tyres": "Neumáticos",
        "incidents": "Incidentes",
        "penalties": "Penalizaciones, banderas y contactos",
        "vs": "Contra la última vez",
        "notes": "No se pudo calcular",
    },
    "en": {
        "title": "Session debrief",
        "result": "Result",
        "pace": "Pace",
        "stints": "Stints and fuel",
        "loss": "Where the time went",
        "tyres": "Tyres",
        "incidents": "Incidents",
        "penalties": "Penalties, flags and contacts",
        "vs": "Against last time",
        "notes": "Could not be computed",
    },
}
_FLAG_ES = {
    "blue": "azul",
    "yellow": "amarilla",
    "black": "negra",
    "white": "blanca",
    "penalty": "de penalización (blanquinegra)",
}
_NOTE_ES = {
    "pace": "ritmo: no hay ninguna vuelta válida",
    "time_loss": "dónde se fue el tiempo: faltan vueltas comparables o la línea de carrera de la pista",
    "tyres": "neumáticos: faltan datos",
}
_SESSION_ES = {
    "practice": "práctica",
    "qualify": "clasificación",
    "race": "carrera",
    "hotlap": "vuelta rápida",
}


def _corner(c: dict) -> str:
    label = f"curva {c['corner']}" if c.get("corner") is not None else "una curva sin número"
    name = c.get("name") or ""
    if re.fullmatch(r"(?i)curva\s+\d+", name.strip()):  # the table's placeholder, not a name
        name = ""
    return f"{name} ({label})" if name else label


def to_markdown(d: Debrief, lang: str = "es") -> str:
    es = lang == "es"
    t = _T["es" if es else "en"]
    f = d.facts
    out = [f"# {t['title']}", ""]
    r = f.get("result", {})
    kind = (
        _SESSION_ES.get(r.get("session", ""), r.get("session", "")) if es else r.get("session", "")
    )
    line = f"{kind}, {r.get('track', '?')}, {r.get('car', '?')}: {r.get('laps', 0)} " + (
        "vueltas" if es else "laps"
    )
    if "position_end" in r and r.get("session") == "race":
        line += (
            f". Salida {r['position_start']}.º, llegada {r['position_end']}.º"
            if es
            else f". Started {r['position_start']}, finished {r['position_end']}"
        )
    elif "position_end" in r and r.get("session") == "qualify":
        line += (
            f". Posición final {r['position_end']}.º"
            if es
            else f". Final position {r['position_end']}"
        )
    out += [f"## {t['result']}", line + ".", ""]
    p = f.get("pace")
    if p:
        out += [f"## {t['pace']}"]
        s = (
            f"Mejor vuelta {_fmt_ms(p['best_ms'])} (la {p['best_lap']}), sobre {p['valid_laps']} vueltas válidas."
            if es
            else f"Best lap {_fmt_ms(p['best_ms'])} (lap {p['best_lap']}), over {p['valid_laps']} valid laps."
        )
        out.append(s)
        if "ideal_ms" in p:
            out.append(
                f"Vuelta ideal (suma de los mejores sectores) {_fmt_ms(p['ideal_ms'])}: {p['ideal_gap_ms'] / 1000:.3f} s por debajo."
                if es
                else f"Ideal lap (sum of the best sectors) {_fmt_ms(p['ideal_ms'])}: {p['ideal_gap_ms'] / 1000:.3f} s below."
            )
        if "consistency_ms" in p:
            out.append(
                f"Regularidad: desviación de {p['consistency_ms'] / 1000:.3f} s sobre {p['consistency_laps']} vueltas."
                if es
                else f"Consistency: {p['consistency_ms'] / 1000:.3f} s deviation over {p['consistency_laps']} laps."
            )
        out.append("")
    st = f.get("stints")
    if st:
        out += [f"## {t['stints']}"]
        for s in st["stints"]:
            tr = (
                f", tendencia {s['trend_s_per_lap']:+.3f} s por vuelta ({s['trend_laps']} vueltas)"
                if "trend_s_per_lap" in s and es
                else (
                    f", trend {s['trend_s_per_lap']:+.3f} s per lap ({s['trend_laps']} laps)"
                    if "trend_s_per_lap" in s
                    else ""
                )
            )
            out.append(
                f"* {s['compound'] or '?'}: {s['laps']} " + ("vueltas" if es else "laps") + tr
            )
        if "fuel_per_lap" in st:
            out.append(
                f"Combustible: {st['fuel_per_lap']:.2f} litros por vuelta (sobre {st['fuel_laps']} vueltas)."
                if es
                else f"Fuel: {st['fuel_per_lap']:.2f} litres per lap (over {st['fuel_laps']} laps)."
            )
        out.append("")
    tl = f.get("time_loss")
    if tl:
        out += [f"## {t['loss']}"]
        for c in tl["corners"]:
            b = (
                (
                    f", frenando {abs(c['brake_earlier_m']):.0f} m "
                    + ("antes" if c["brake_earlier_m"] > 0 else "después")
                )
                if c["brake_earlier_m"] is not None and es
                else (
                    f", braking {abs(c['brake_earlier_m']):.0f} m "
                    + ("earlier" if c["brake_earlier_m"] > 0 else "later")
                    if c["brake_earlier_m"] is not None
                    else ""
                )
            )
            out.append(
                f"* {_corner(c)}: {c['mean_loss_s']:.2f} s de media contra tu mejor vuelta ({c['laps']} vueltas){b}"
                if es
                else f"* {_corner(c)}: {c['mean_loss_s']:.2f} s on average against your best lap ({c['laps']} laps){b}"
            )
        out.append("")
    ty = f.get("tyres")
    if ty:
        out += [f"## {t['tyres']}"]
        if "wear_used" in ty:
            used = ", ".join(f"{w} {v:.1f}" for w, v in ty["wear_used"].items())
            out.append(
                f"Desgaste usado (puntos de vida): {used}."
                if es
                else f"Wear used (life points): {used}."
            )
        for axle in ("front", "rear"):
            if f"temp_{axle}" in ty:
                tt = ty[f"temp_{axle}"]
                out.append(
                    f"Temperatura {'delanteros' if axle == 'front' else 'traseros'}: {tt['min']} a {tt['max']} °C."
                    if es
                    else f"{axle.capitalize()} temperature: {tt['min']} to {tt['max']} °C."
                )
        ci = ty.get("corner_index")
        if ci:
            tag = "estimación" if es else "estimate"
            for axle in ("front", "rear"):
                if ci.get(axle):
                    top = ", ".join(f"{_corner(c)} {c['share_pct']:.0f} %" for c in ci[axle])
                    out.append(
                        f"Donde más se trabajan los {'delanteros' if axle == 'front' else 'traseros'} ({tag}, {ci['laps']} vueltas): {top}."
                        if es
                        else f"Where the {axle} tyres work hardest ({tag}, {ci['laps']} laps): {top}."
                    )
        out.append("")
    vs = f.get("vs_last")
    if vs:
        out += [f"## {t['vs']} ({vs['previous_date']})"]
        d_ms = vs["best_delta_ms"]
        word = (
            ("más rápida" if d_ms < 0 else "más lenta")
            if es
            else ("faster" if d_ms < 0 else "slower")
        )
        out.append(
            f"Tu mejor vuelta es {abs(d_ms) / 1000:.3f} s {word} que la de entonces."
            if es
            else f"Your best lap is {abs(d_ms) / 1000:.3f} s {word} than then."
        )
        if "consistency_delta_ms" in vs:
            c = vs["consistency_delta_ms"]
            out.append(
                f"Regularidad: {abs(c) / 1000:.3f} s {'mejor' if c < 0 else 'peor'}."
                if es
                else f"Consistency: {abs(c) / 1000:.3f} s {'better' if c < 0 else 'worse'}."
            )
        if not vs["same_compound"]:
            out.append(
                "Ojo: el compuesto no era el mismo." if es else "Note: not the same compound."
            )
        out.append("")
    inc = f.get("incidents")
    if inc:
        out += [
            f"## {t['incidents']}",
            (
                f"Vueltas con incidente: {', '.join(map(str, inc['laps_with_incidents']))}."
                if es
                else f"Laps with an incident: {', '.join(map(str, inc['laps_with_incidents']))}."
            ),
            "",
        ]
    pe = f.get("penalties")
    if pe:
        out += [f"## {t['penalties']}"]
        if "max_penalty_s" in pe:
            out.append(
                f"Penalización de {pe['max_penalty_s']:.0f} s."
                if es
                else f"A {pe['max_penalty_s']:.0f} s penalty."
            )
        if "flags" in pe:
            shown = [_FLAG_ES.get(x, x) if es else x for x in pe["flags"]]
            out.append(("Banderas: " if es else "Flags: ") + ", ".join(shown) + ".")
        if "contacts" in pe:
            n = pe["contacts"]
            out.append(
                f"{n} contacto{'s' if n != 1 else ''}; el peor, de {pe['worst_contact']:.0f} km/h."
                if es
                else f"{n} contact{'s' if n != 1 else ''}; the worst at {pe['worst_contact']:.0f} km/h."
            )
        out.append("")
    notes = [(_NOTE_ES.get(n, n) if es else n) for n in d.notes]
    if notes:
        out += [f"## {t['notes']}"] + [f"* {n}" for n in notes] + [""]
    text = "\n".join(out).rstrip() + "\n"
    return re.sub(r"(?<=\d)\.(?=\d)", ",", text) if es else text
