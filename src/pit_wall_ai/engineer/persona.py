"""The engineer's identity and manner of speaking (phase 6b).

A persona changes **how things are said**, never **what is true**: it is a few lines added to the model's
instructions (name, the driver's name, a style) and it does not touch the numbers, the rules or the grounding
check. The built-in styles are descriptions of a manner (brief, warm, technical...), not imitations of any
real person; a user can write their own in ``engineer.persona.custom``.
"""

from __future__ import annotations

from dataclasses import dataclass

STYLES: dict[str, dict[str, str]] = {
    "neutral": {
        "es": "",
        "en": "",
    },
    "brief": {
        "es": "Estilo: lo más breve posible; una sola frase si basta, sin saludos ni adornos.",
        "en": "Style: as brief as possible; a single sentence when it is enough, no greetings or frills.",
    },
    "calm": {
        "es": "Estilo: sereno y tranquilizador; tono pausado, sin dramatizar los problemas, "
        "y di primero lo que está bien.",
        "en": "Style: calm and reassuring; unhurried, never dramatising problems, "
        "and mention what is going well first.",
    },
    "technical": {
        "es": "Estilo: técnico y preciso; usa el vocabulario de la ingeniería de pista y da los "
        "valores con su unidad antes de la conclusión.",
        "en": "Style: technical and precise; use race-engineering vocabulary and give the values "
        "with their unit before the conclusion.",
    },
    "warm": {
        "es": "Estilo: cercano y animado; trata al piloto por su nombre de vez en cuando, "
        "reconoce lo que hace bien y mantén el ánimo.",
        "en": "Style: friendly and upbeat; use the driver's name from time to time, "
        "acknowledge what they do well and keep morale up.",
    },
    "direct": {
        "es": "Estilo: directo y exigente; ve al grano, señala el error sin rodeos y di qué hacer.",
        "en": "Style: direct and demanding; get to the point, name the mistake plainly and say what to do.",
    },
}

_DE_STYLES = {
    "neutral": "",
    "brief": "Stil: so kurz wie möglich; ein einziger Satz, wenn er reicht, ohne Begrüßung und Schnörkel.",
    "calm": "Stil: ruhig und beruhigend; gelassen, ohne Probleme zu dramatisieren, und zuerst nennen, was gut läuft.",
    "technical": "Stil: technisch und präzise; Rennsport-Fachsprache, die Werte mit Einheit vor dem Schluss.",
    "warm": "Stil: freundlich und aufmunternd; den Fahrer ab und zu mit Namen ansprechen, Gutes anerkennen und die Stimmung hochhalten.",
    "direct": "Stil: direkt und fordernd; auf den Punkt, den Fehler klar benennen und sagen, was zu tun ist.",
}
for _k, _v in _DE_STYLES.items():
    STYLES[_k]["de"] = _v

DEFAULT_NAME = {
    "es": "tu ingeniero de pista",
    "en": "your race engineer",
    "de": "dein Renningenieur",
}


@dataclass(frozen=True)
class Persona:
    name: str | None = None
    driver_name: str | None = None
    style: str = "neutral"
    custom: str | None = None

    def known_style(self) -> bool:
        return self.style in STYLES

    def display_name(self, lang: str) -> str:
        return self.name or DEFAULT_NAME.get(lang, DEFAULT_NAME["en"])

    def system_suffix(self, lang: str) -> str:
        """Lines appended to the model's instructions; empty for the neutral default."""
        lang = lang if lang in ("es", "en", "de") else "en"
        parts: list[str] = []
        if self.name:
            parts.append(
                {
                    "es": f"Te llamas {self.name}.",
                    "en": f"Your name is {self.name}.",
                    "de": f"Du heißt {self.name}.",
                }[lang]
            )
        if self.driver_name:
            parts.append(
                {
                    "es": f"El piloto se llama {self.driver_name}.",
                    "en": f"The driver is called {self.driver_name}.",
                    "de": f"Der Fahrer heißt {self.driver_name}.",
                }[lang]
            )
        style = STYLES.get(self.style, STYLES["neutral"])[lang]
        if style:
            parts.append(style)
        if self.custom and self.custom.strip():
            head = {
                "es": "Indicaciones de estilo del piloto",
                "en": "Style notes from the driver",
                "de": "Stilhinweise des Fahrers",
            }[lang]
            parts.append(f"{head}: {self.custom.strip()}")
        if parts:
            parts.append(
                {
                    "es": "El estilo nunca cambia los datos: los números y los hechos son los de CONTEXT.",
                    "en": "Style never changes the data: numbers and facts are the ones in CONTEXT.",
                    "de": "Der Stil ändert nie die Daten: Zahlen und Fakten sind die aus CONTEXT.",
                }[lang]
            )
        return " ".join(parts)

    def greeting(self, lang: str, voice_name: str | None = None) -> str:
        """The introduction used by the voice test; the persona's name wins over the voice's."""
        who = self.name or voice_name
        if lang == "de":
            return (
                f"Hallo, ich bin {who} und werde dein Renningenieur sein."
                if who
                else "Hallo, ich werde dein Renningenieur sein."
            )
        if lang == "es":
            return (
                f"Hola, soy {who} y seré tu ingeniero de pista."
                if who
                else "Hola, seré tu ingeniero de pista."
            )
        return (
            f"Hello, I am {who} and I will be your race engineer."
            if who
            else "Hello, I will be your race engineer."
        )


def from_config(cfg) -> Persona:
    """``cfg`` is :class:`pit_wall_ai.config.PersonaConfig`."""
    return Persona(cfg.name, cfg.driver_name, cfg.style, cfg.custom)
