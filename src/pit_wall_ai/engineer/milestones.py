"""Milestones of a session: the pole, the grid and the result, said the way an engineer would.

What is detected (all from fields already read and observed on a real qualifying and race, see
``docs/ENGINEER_EXPERIENCE_PLAN.md``):

* **qualifying**: the player's ``position`` is the live classification by best time. After a lap that puts the
  car first: *provisional pole*. When the qualifying session is left (the game moves on to the race or the
  menu) the last position is the **grid**: pole, front row, last, or position N.
* **race**: the position at the first movement is the **starting position**; when the laps are done
  (``completed_laps >= number_of_laps``, or the timer is over and the next lap closes) the **result**, with
  the places gained or lost.

What is said: the **facts** are computed here (positions, places gained, cars in the field). The *wording* is
asked from the model so it is new every time; its answer is accepted only if every number in it is one of the
facts. Without the model (no key, offline, a refused sentence) a pool of fixed variants is used, never the same
one twice in a row. Nothing here is a number the model invented.
"""

from __future__ import annotations

import logging
import random
import re
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from ..events import LapCompleted, SessionChanged

log = logging.getLogger(__name__)

MOVING_KMH = 10.0


@dataclass(frozen=True)
class Milestone:
    kind: str  # quali_provisional | quali_final | race_finished
    situation: str  # pole | front_row | last | other | win | podium | gained | held | lost
    facts: dict = field(
        default_factory=dict
    )  # position, start_position, num_cars, places_gained, laps


# ── the detector ──────────────────────────────────────────────────────────────────────────────────────────
class MilestoneDetector:
    """Feed it every sample (with the session events); it returns the milestones that just happened."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._kind = "unknown"
        self._pos = 0
        self._num_cars = 0
        self._start_pos: int | None = None
        self._moved = False
        self._provisional_said = False
        self._finished = False
        self._quali_done = False
        self._grid_pos = 0

    def update(self, s, state, events, board=None) -> list[Milestone]:
        out: list[Milestone] = []
        for e in events:
            if isinstance(e, SessionChanged):
                if self._kind == "qualify" and self._pos > 0 and not self._quali_done:
                    out.append(self._quali_final())
                self.reset()
        if state.status == "off":  # the menu: leaving a qualifying this way also ends it
            if self._kind == "qualify" and self._pos > 0 and not self._quali_done:
                self._quali_done = True
                out.append(self._quali_final())
            return out
        if state.status == "replay":
            return out
        kind = state.session
        pos = int(s.position or 0)
        cars = int(getattr(s.static, "num_cars", 0) or 0)
        self._kind = kind
        if pos > 0:
            self._pos = pos
        if cars > 0:
            self._num_cars = cars
        if kind == "qualify":
            for e in events:
                if (
                    isinstance(e, LapCompleted)
                    and getattr(e.lap, "time_ms", 0) > 0
                    and e.is_best
                    and self._pos == 1
                    and not self._provisional_said
                    and not getattr(e.lap, "out_lap", False)
                ):
                    self._provisional_said = True
                    out.append(
                        Milestone(
                            "quali_provisional", "pole", {"position": 1, "num_cars": self._num_cars}
                        )
                    )
        elif kind == "race":
            if not self._moved:
                if s.speed_kmh >= MOVING_KMH:
                    self._moved = True
                    self._start_pos = self._grid_pos or self._pos
                elif self._pos:
                    self._grid_pos = self._pos  # the last position seen on the grid
            if self._finished:
                return out
            total = int(s.number_of_laps or 0)
            done = int(s.completed_laps or 0)
            finished = total > 0 and done >= total and self._moved
            # a race by time has no lap count: the bridge says when my car has taken the chequered flag
            if not finished and board is not None and getattr(board, "me", None) is not None:
                finished = bool(board.me.finished) and self._moved
            if finished:
                self._finished = True
                out.append(self._race_result(done))
        return out

    def _quali_final(self) -> Milestone:
        pos, cars = self._pos, self._num_cars
        if pos == 1:
            sit = "pole"
        elif pos == 2:
            sit = "front_row"
        elif cars and pos >= cars:
            sit = "last"
        else:
            sit = "other"
        return Milestone("quali_final", sit, {"position": pos, "num_cars": cars})

    def _race_result(self, laps: int) -> Milestone:
        pos, cars = self._pos, self._num_cars
        start = self._start_pos or 0
        gained = (start - pos) if start and pos else 0
        if pos == 1:
            sit = "win"
        elif pos <= 3:
            sit = "podium"
        elif gained > 0:
            sit = "gained"
        elif gained == 0:
            sit = "held"
        else:
            sit = "lost"
        facts = {
            "position": pos,
            "start_position": start,
            "places_gained": gained,
            "num_cars": cars,
            "laps": laps,
        }
        if start and cars and start >= cars:
            facts["started_last"] = True
        return Milestone("race_finished", sit, facts)


# ── the wording ───────────────────────────────────────────────────────────────────────────────────────────
VARIANTS: dict[str, dict[str, dict[str, list[str]]]] = {
    "es": {
        "quali_provisional": {
            "pole": [
                "Tenemos la pole provisional, buena vuelta.",
                "Primer puesto provisional. Vamos a ver si nos la quitan.",
                "Ese tiempo nos pone primeros por ahora, la pole provisional es nuestra.",
            ],
        },
        "quali_final": {
            "pole": [
                "Aseguramos la pole, saldremos desde el primer puesto.",
                "Pole confirmada. Mañana, perdón, ahora toca convertirla en victoria.",
                "Nadie nos bajó de ahí: pole position, saldremos primeros.",
            ],
            "front_row": [
                "Saldremos desde la primera línea, segundo puesto. Muy buen trabajo.",
                "Segundos en la parrilla, en primera fila. Está todo por jugarse.",
            ],
            "last": [
                "Saldremos últimos, no pasa nada, intentaremos remontar.",
                "Último en la parrilla. Toca una carrera de remontada, con cabeza.",
            ],
            "other": [
                "Saldremos desde el puesto {position}. Hay margen para remontar en la carrera.",
                "Parrilla: puesto {position} de {num_cars}. Vamos a por ello en la salida.",
            ],
        },
        "race_finished": {
            "win": [
                "¡Ganamos! Enhorabuena, gran carrera de principio a fin.",
                "Victoria, primer puesto. Muy bien hecho.",
            ],
            "win_from_back": [
                "Ganamos saliendo desde la última posición: una remontada histórica, directo a los libros.",
                "Primero tras salir último. Esto se recuerda mucho tiempo.",
            ],
            "podium": [
                "Eso es un podio, muy bien. Puesto {position}.",
                "Podio, tercer escalón o mejor: puesto {position}. Orgulloso del trabajo.",
            ],
            "gained": [
                "Felicidades por esa remontada: salimos {start_position} y llegamos {position}, ganamos {places_gained} puestos.",
                "Remontada sólida, del {start_position} al {position}. Deberías estar orgulloso.",
            ],
            "gained_from_back": [
                "Felicidades por esa remontada, salimos últimos y llegamos en el puesto {position}. Deberías estar orgulloso.",
            ],
            "held": [
                "Terminamos en el puesto {position}, donde salimos. Carrera limpia.",
                "Puesto {position}, igual que la salida. Hay cosas que pulir para la próxima.",
            ],
            "lost": [
                "Terminamos {position}, perdimos {places_lost} puestos. Revisaremos qué pasó, la próxima va mejor.",
                "No fue nuestra carrera: puesto {position}. A analizarla con calma.",
            ],
        },
    },
    "en": {
        "quali_provisional": {
            "pole": [
                "Provisional pole, great lap.",
                "P1 for now, let us see if they take it from us.",
            ],
        },
        "quali_final": {
            "pole": [
                "We have secured pole, we start from first.",
                "Pole position confirmed. Now let us make it a win.",
            ],
            "front_row": ["Front row, P2 on the grid. Well done."],
            "last": ["We start last, no problem, we will work our way up."],
            "other": ["We start P{position} of {num_cars}. Plenty of room to move up."],
        },
        "race_finished": {
            "win": ["We won! Congratulations, a great race."],
            "win_from_back": [
                "We won from the back of the grid: a historic comeback, straight into the books."
            ],
            "podium": ["That is a podium, well done. P{position}."],
            "gained": [
                "Congratulations on that comeback: we started P{start_position} and finished P{position}."
            ],
            "gained_from_back": [
                "Congratulations on the comeback, we started last and finished P{position}. Be proud."
            ],
            "held": ["Finished P{position}, where we started. A clean race."],
            "lost": [
                "Finished P{position}, down {places_lost} places. We will go through it, next one is better."
            ],
        },
    },
    "de": {
        "quali_provisional": {
            "pole": [
                "Vorläufige Pole, tolle Runde.",
                "Vorerst P1, mal sehen, ob sie uns die Pole abnehmen.",
            ],
        },
        "quali_final": {
            "pole": [
                "Pole gesichert, wir starten von Platz eins.",
                "Pole-Position bestätigt. Jetzt machen wir daraus einen Sieg.",
            ],
            "front_row": ["Erste Reihe, Startplatz zwei. Sehr gut gemacht."],
            "last": ["Wir starten von ganz hinten, kein Problem, wir kämpfen uns nach vorn."],
            "other": ["Wir starten von Platz {position} von {num_cars}. Es ist noch viel möglich."],
        },
        "race_finished": {
            "win": ["Wir haben gewonnen! Glückwunsch, ein starkes Rennen."],
            "win_from_back": [
                "Sieg vom letzten Startplatz: eine historische Aufholjagd, direkt in die Geschichtsbücher."
            ],
            "podium": ["Das ist ein Podium, sehr gut. Platz {position}."],
            "gained": [
                "Glückwunsch zur Aufholjagd: Start von Platz {start_position}, Ziel auf Platz {position}."
            ],
            "gained_from_back": [
                "Glückwunsch zur Aufholjagd, wir starteten als Letzte und sind auf Platz {position} angekommen. Sei stolz."
            ],
            "held": ["Platz {position}, wie beim Start. Ein sauberes Rennen."],
            "lost": [
                "Platz {position}, {places_lost} Plätze verloren. Wir schauen es uns an, das nächste wird besser."
            ],
        },
    },
}

SYSTEM = {
    "es": (
        "Eres el ingeniero de carrera de un piloto de simracing y hablas por radio. Escribe UN mensaje de "
        "una o dos frases cortas, natural y distinto cada vez, para el momento que te indico. Usa SOLO los "
        "hechos del bloque CONTEXT: no inventes tiempos, nombres ni rivales. Si mencionas una posición o "
        "una cantidad, escríbela en cifras tal como está en CONTEXT. Si el piloto salió último y ganó o "
        "remontó, que se note. Nada de emojis."
    ),
    "en": (
        "You are the race engineer of a simracing driver and speak over the radio. Write ONE message of one "
        "or two short sentences, natural and different every time, for the moment described. Use ONLY the "
        "facts in the CONTEXT block: do not invent times, names or rivals. Write any position or quantity "
        "as digits exactly as in CONTEXT. If the driver started last and won or climbed, make that count. "
        "No emojis."
    ),
    "de": (
        "Du bist der Renningenieur eines Simracing-Fahrers und sprichst über Funk. Schreibe EINE Nachricht "
        "aus ein bis zwei kurzen Sätzen, natürlich und jedes Mal anders, für den beschriebenen Moment. "
        "Benutze NUR die Fakten im Block CONTEXT: erfinde keine Zeiten, Namen oder Gegner. Schreibe "
        "Positionen und Mengen als Ziffern genau wie in CONTEXT. Wenn der Fahrer als Letzter gestartet ist "
        "und gewonnen oder aufgeholt hat, soll man das merken. Keine Emojis."
    ),
}
_ANGLES = {
    "es": ["orgulloso", "sereno", "enérgico", "cercano", "breve y directo", "con humor ligero"],
    "en": ["proud", "calm", "energetic", "warm", "short and direct", "with light humour"],
    "de": ["stolz", "gelassen", "energisch", "herzlich", "kurz und direkt", "mit leichtem Humor"],
}


def pick_variant_key(m: Milestone) -> str:
    f = m.facts
    if m.kind == "race_finished":
        if m.situation == "win" and f.get("started_last"):
            return "win_from_back"
        if m.situation == "gained" and f.get("started_last"):
            return "gained_from_back"
    return m.situation


def allowed_numbers(m: Milestone) -> set[int]:
    nums = {
        int(v) for v in m.facts.values() if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    gained = m.facts.get("places_gained")
    if isinstance(gained, (int, float)):
        nums.add(abs(int(gained)))
    return nums | {1}


def text_is_grounded(text: str, m: Milestone) -> bool:
    """Every number in the sentence is a fact of the milestone (ordinals like 5ta / 5.º count by their digits)."""
    ok = allowed_numbers(m)
    return all(int(d) in ok for d in re.findall(r"\d+", text))


class MilestoneAnnouncer:
    """Turns milestones into spoken sentences, off the telemetry thread."""

    def __init__(
        self,
        speaker,
        *,
        language: str = "es",
        client=None,
        persona_suffix: str = "",
        quiet: Callable[[], bool] = lambda: False,
        rng: random.Random | None = None,
        threaded: bool = True,
    ) -> None:
        self.speaker = speaker
        self.language = language if language in VARIANTS else "en"
        self.client = client
        self.persona_suffix = persona_suffix
        self.quiet = quiet
        self._rng = rng or random.Random()
        self._recent: deque[str] = deque(maxlen=6)
        self._last_variant: dict[str, str] = {}
        self._threaded = threaded
        self.said: list[tuple[Milestone, str, str]] = []  # (milestone, text, "model" | "variant")

    def announce(self, m: Milestone) -> None:
        if self._threaded:
            threading.Thread(target=self._run, args=(m,), name="milestone", daemon=True).start()
        else:
            self._run(m)

    # -- internals ---------------------------------------------------------------------------------------
    def _run(self, m: Milestone) -> None:
        try:
            text, source = self._text(m)
        except Exception:  # noqa: BLE001 - a congratulation must never break anything
            log.exception("milestone text failed")
            return
        if not text or self.quiet():
            return
        self._recent.append(text)
        self.said.append((m, text, source))
        self.speaker.say(
            text, interrupt=False, meta={"rule": f"milestone:{m.kind}", "source": source}
        )

    def _text(self, m: Milestone) -> tuple[str, str]:
        if self.client is not None:
            t = self._from_model(m)
            if t:
                return t, "model"
        return self._from_variants(m), "variant"

    def _from_model(self, m: Milestone) -> str | None:
        from .llm import LLMRequest

        lang = self.language
        angle = self._rng.choice(_ANGLES[lang])
        what = {
            "quali_provisional": "the driver has just set a lap that puts the car provisionally first",
            "quali_final": "the qualifying session has ended; this is the grid position",
            "race_finished": "the race has ended; this is the result",
        }[m.kind]
        facts = dict(m.facts)
        if "places_gained" in facts and facts["places_gained"] < 0:
            facts["places_lost"] = -facts["places_gained"]
        user = (
            f"Moment: {what}. Situation: {m.situation}. Tone: {angle}. "
            "Avoid repeating these recent sentences: " + " | ".join(self._recent)
        )
        system = SYSTEM[lang] + (" " + self.persona_suffix if self.persona_suffix else "")
        resp = self.client.ask(LLMRequest(system=system, user=user, context=facts), urgent=False)
        if resp is None:
            return None
        text = resp.text.strip()
        if not text or len(text) > 280 or not text_is_grounded(text, m) or text in self._recent:
            return None
        return text

    def _from_variants(self, m: Milestone) -> str:
        pool = VARIANTS[self.language].get(m.kind, {})
        options = pool.get(pick_variant_key(m)) or pool.get(m.situation) or []
        if not options:
            return ""
        key = f"{m.kind}:{m.situation}"
        choices = [o for o in options if o != self._last_variant.get(key)] or options
        chosen = self._rng.choice(choices)
        self._last_variant[key] = chosen
        facts = dict(m.facts)
        facts["places_lost"] = max(0, -int(facts.get("places_gained", 0) or 0))
        try:
            return chosen.format(**facts)
        except (KeyError, IndexError):
            return chosen


def attach(
    engine, speaker, cfg, *, client=None, quiet: Callable[[], bool] = lambda: False, threaded=True
):
    """Hook the detector and the announcer on a :class:`SessionEngine`; returns the announcer (or ``None``)."""
    mc = cfg.engineer.milestones
    if not mc.enabled:
        return None
    from .persona import from_config

    ann = MilestoneAnnouncer(
        speaker,
        language=cfg.engineer.language,
        client=client if mc.use_model else None,
        persona_suffix=from_config(cfg.engineer.persona).system_suffix(cfg.engineer.language),
        quiet=quiet,
        threaded=threaded,
    )
    det = MilestoneDetector()

    def hook(snap, state, tracker, events):
        from ..telemetry.csp_bridge import current_board

        for m in det.update(snap, state, events, current_board()):
            ann.announce(m)

    engine.add_sample_hook(hook)
    return ann
