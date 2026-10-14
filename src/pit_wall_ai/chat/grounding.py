"""Grounding: is every number of a reply in the context, and about the right thing?

Necessary, not sufficient. It catches (1) a number that is nowhere in the context and (2) a number that *is*
there but belongs to another wheel, corner, lap or quantity than the sentence says. It cannot judge a wrong
*interpretation* of a correct number; that is what short answers and the evaluation set are for.

Accepted as equivalent: ``1,9`` / ``1.9`` / ``1 coma 9``, a value rounded to the precision it is written with
(1.87 may be written 1.9 or 2, not 1.8), ``1:24,6`` for 84.6 s, ``84889`` / ``84,889 s`` for the same lap time,
and the sign (a delta written without its minus). Words are not checked, only digits.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .facts import Fact, FactSheet, Subject, norm

_TIME = re.compile(r"(?<![\w.,:])(\d+):(\d{2})(?:[.,](\d+))?(?![\w])")
_COMA = re.compile(r"(?<![\w.,])(\d+)\s+coma\s+(\d+)(?![\w])", re.IGNORECASE)
_NUM = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?![\w]|[.,]\d)")
_SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")

_UNIT_WORDS = {
    "c": "C",
    "°c": "C",
    "grados": "C",
    "celsius": "C",
    "psi": "psi",
    "l": "L",
    "litros": "L",
    "litres": "L",
    "liters": "L",
    "litro": "L",
    "s": "s",
    "seg": "s",
    "segundos": "s",
    "seconds": "s",
    "sec": "s",
    "segundo": "s",
    "ms": "ms",
    "km/h": "km/h",
    "kmh": "km/h",
    "kph": "km/h",
    "m": "m",
    "metros": "m",
    "meters": "m",
    "metres": "m",
    "metro": "m",
    "%": "%",
    "vueltas": "laps",
    "vuelta": "laps",
    "laps": "laps",
    "lap": "laps",
}


@dataclass(frozen=True)
class Violation:
    kind: str  # "unsupported" | "misattributed" | "unit"
    text: str  # the number as written
    sentence: str
    detail: str


@dataclass
class GroundingReport:
    violations: list[Violation] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class _Number:
    value: float
    decimals: int
    start: int
    end: int
    text: str
    unit: str = ""


def _fold(text: str) -> str:
    """Lowercase, accents removed, anything not alphanumeric a space: same length as the input."""
    out = []
    for ch in text:
        d = unicodedata.normalize("NFKD", ch)
        base = (d[0] if d else ch).lower()
        out.append(base if base.isalnum() else " ")
    return "".join(out)


def _decimals(s: str | None) -> int:
    return len(s) if s else 0


def _numbers(sentence: str) -> list[_Number]:
    found: list[_Number] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    for m in _TIME.finditer(sentence):
        mins, secs, frac = int(m.group(1)), int(m.group(2)), m.group(3)
        value = mins * 60 + secs + (float("0." + frac) if frac else 0.0)
        found.append(_Number(value, _decimals(frac), m.start(), m.end(), m.group(0), "s"))
        taken.append((m.start(), m.end()))
    for m in _COMA.finditer(sentence):
        if free(m.start(), m.end()):
            value = float(f"{m.group(1)}.{m.group(2)}")
            found.append(_Number(value, len(m.group(2)), m.start(), m.end(), m.group(0)))
            taken.append((m.start(), m.end()))
    for m in _NUM.finditer(sentence):
        if not free(m.start(), m.end()):
            continue
        raw = m.group(1).replace(",", ".")
        nxt = sentence[m.end() : m.end() + 2]
        if nxt.startswith(("º", "ª", ".º", ".ª")):  # an ordinal: 1.º, 3.ª
            continue
        dec = len(raw.split(".")[1]) if "." in raw else 0
        found.append(_Number(float(raw), dec, m.start(), m.end(), m.group(0)))
        taken.append((m.start(), m.end()))
    found.sort(key=lambda n: n.start)
    out = []
    for n in found:  # the unit that follows, if one is written
        if n.unit:
            out.append(n)
            continue
        tail = sentence[n.end : n.end + 12].lower().lstrip()
        m = re.match(r"(°\s?c|km/h|kmh|kph|%|[a-záéíóúñ]+)", tail)
        unit = _UNIT_WORDS.get(m.group(1).replace(" ", ""), "") if m else ""
        out.append(_Number(n.value, n.decimals, n.start, n.end, n.text, unit))
    return out


def _equal(n: float, decimals: int, v: float) -> bool:
    """``n`` (as written, with its decimals) is the value ``v`` rounded to that precision, sign ignored."""
    a, b = abs(n), abs(v)
    if round(b, decimals) == round(a, decimals):
        return True
    return abs(a - b) <= 0.5 * 10 ** (-decimals) + 1e-9


def _unit_ok(written: str, fact: Fact) -> bool:
    if not written or not fact.unit:
        return True
    if written == "ms":
        return fact.unit == "s"  # lap times carry their milliseconds
    return written == fact.unit or (written == "laps" and fact.unit in ("laps", ""))


def _mentions(sheet: FactSheet, folded: str) -> list[tuple[int, int, Subject]]:
    """(start, end, subject) of every alias of every known subject found in the sentence."""
    out: list[tuple[int, int, Subject]] = []
    for subject, names in sheet.aliases.items():
        for name in names:
            pat = (
                r"(?<![a-z0-9])" + r"\s+".join(re.escape(w) for w in name.split()) + r"(?![a-z0-9])"
            )
            for m in re.finditer(pat, folded):
                out.append((m.start(), m.end(), subject))
    # an alias contained in a longer one at the same place ("tornantino" inside "esse tornantino"): keep the longest
    out.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    kept: list[tuple[int, int, Subject]] = []
    for s, e, sub in out:
        if any(ks <= s and e <= ke and (ke - ks) > (e - s) for ks, ke, _ in kept):
            continue
        kept.append((s, e, sub))
    return kept


def check(reply: str, sheet: FactSheet, question: str = "") -> GroundingReport:
    report = GroundingReport()
    allowed_extra = [abs(x) for x in sheet.extra_numbers]
    for n in _numbers(question):
        allowed_extra.append(abs(n.value))
    for sentence in (s.strip() for s in _SENTENCE.split(reply) if s.strip()):
        folded = _fold(sentence)
        mentions = _mentions(sheet, folded)
        for num in _numbers(sentence):
            # a number that is part of a subject mention ("curva 6", "vuelta 12") is a reference, not a claim
            if any(s <= num.start and num.end <= e for s, e, _ in mentions):
                continue
            report.checked += 1
            cands = [
                f
                for f in sheet.facts
                if any(_equal(num.value, num.decimals, v) for v in f.accepts())
            ]
            if not cands:
                if any(_equal(num.value, num.decimals, x) for x in allowed_extra):
                    continue
                report.violations.append(
                    Violation("unsupported", num.text, sentence, "the number is not in the context")
                )
                continue
            unit_ok = [f for f in cands if _unit_ok(num.unit, f)]
            if not unit_ok:
                units = sorted({f.unit for f in cands if f.unit})
                report.violations.append(
                    Violation(
                        "unit",
                        num.text,
                        sentence,
                        f"the number matches a value in {', '.join(units)} but is written with {num.unit!r}",
                    )
                )
                continue
            if any(_subject_fits(f, num, mentions) for f in unit_ok):
                continue
            wrong = next(f for f in unit_ok if f.subject is not None)
            report.violations.append(
                Violation(
                    "misattributed",
                    num.text,
                    sentence,
                    f"{num.text} is the value of {wrong.key} ({_who(wrong.subject)}) but the sentence is "
                    f"about {_nearest(num, mentions, wrong.subject.kind)}",
                )
            )
    return report


def _who(s: Subject | None) -> str:
    return "the session" if s is None else f"{s.kind} {s.id}"


def _nearest(num: _Number, mentions, kind: str) -> str:
    m = _nearest_mention(num, mentions, kind)
    return "something else" if m is None else _who(m)


def _nearest_mention(num: _Number, mentions, kind: str) -> Subject | None:
    same = [(s, e, sub) for s, e, sub in mentions if sub.kind == kind]
    if not same:
        return None
    c = (num.start + num.end) / 2
    return min(same, key=lambda t: abs((t[0] + t[1]) / 2 - c))[2]


def _subject_fits(fact: Fact, num: _Number, mentions) -> bool:
    """A fact without a subject always fits; one with a subject fits when the sentence does not talk about
    that kind of subject at all, or when the mention nearest to the number is that very subject."""
    if fact.subject is None:
        return True
    near = _nearest_mention(num, mentions, fact.subject.kind)
    return near is None or near == fact.subject


def sanitize(reply: str, report: GroundingReport, lang: str = "es") -> str:
    """The reply without the sentences that carry a violation; if nothing is left, an honest refusal."""
    if report.ok:
        return reply
    bad = {v.sentence for v in report.violations}
    kept = [s for s in (x.strip() for x in _SENTENCE.split(reply) if x.strip()) if s not in bad]
    if kept:
        return " ".join(kept)
    return (
        "No puedo confirmar ese dato con la información que tengo."
        if lang == "es"
        else "I cannot confirm that with the information I have."
    )


__all__ = ["GroundingReport", "Violation", "check", "sanitize", "norm"]
