"""Greetings, thanks and radio checks: answered on the spot, without the model.

Only a short utterance made of nothing but such words counts ("gracias", "muy buen trabajo, gracias por la
ayuda", "¿me copias?"). Anything that also asks for data goes to the router or the model as before.
"""

from __future__ import annotations

import itertools

from .local import norm

_THANKS = (
    "gracias",
    "thanks",
    "thank you",
    "danke",
    "buen trabajo",
    "buen curro",
    "good job",
    "great job",
)
_GREET = ("hola", "buenas", "buenos dias", "buenas tardes", "hello", "hi", "hey", "hallo", "oye")
_CHECK = ("me copias", "me recibes", "me oyes", "me escuchas", "radio check", "can you hear me")
_FILLER = frozenset(
    {
        "muy",
        "mucho",
        "muchas",
        "muchisimas",
        "por",
        "la",
        "el",
        "lo",
        "las",
        "los",
        "de",
        "ayuda",
        "todo",
        "esto",
        "eso",
        "un",
        "una",
        "buen",
        "buena",
        "buenos",
        "trabajo",
        "curro",
        "bien",
        "genial",
        "perfecto",
        "vale",
        "ok",
        "okay",
        "entendido",
        "recibido",
        "ingeniero",
        "equipo",
        "piloto",
        "tio",
        "very",
        "much",
        "for",
        "the",
        "help",
        "your",
        "you",
        "good",
        "great",
        "job",
        "work",
        "thanks",
        "thank",
        "team",
        "engineer",
    }
)

_REPLIES = {
    "thanks": {
        "es": ("A ti, piloto.", "Para eso estamos.", "De nada, sigue así.", "Recibido."),
        "en": ("Anytime, driver.", "That is what we are here for.", "Copy, keep it up."),
        "de": ("Gern geschehen.", "Dafür sind wir da.", "Verstanden, weiter so."),
    },
    "greet": {
        "es": ("Hola, piloto. Dime.", "Aquí estoy, dime.", "Te escucho."),
        "en": ("Hello, driver. Go ahead.", "I am here, go ahead.", "Listening."),
        "de": ("Hallo, Fahrer. Sprich.", "Ich höre.", "Ich bin da."),
    },
    "check": {
        "es": ("Te copio fuerte y claro.", "Recibido, te oigo bien."),
        "en": ("Loud and clear.", "Copy, I hear you fine."),
        "de": ("Laut und deutlich.", "Verstanden, ich höre dich gut."),
    },
}
_counter = itertools.count()


def _has(q: str, words: tuple[str, ...]) -> bool:
    padded = f" {q} "
    return any(f" {w} " in padded for w in words)


def reply(question: str, lang: str = "es") -> str | None:
    """The answer to a pure greeting, thanks or radio check; ``None`` for anything else."""
    q = " ".join(norm(question).split())
    if not q or len(q.split()) > 9:
        return None
    kinds = [
        k
        for k, words in (("check", _CHECK), ("thanks", _THANKS), ("greet", _GREET))
        if _has(q, words)
    ]
    if not kinds:
        return None
    known = {w for ws in (_THANKS, _GREET, _CHECK) for p in ws for w in p.split()}
    if any(w not in _FILLER and w not in known for w in q.split()):
        return None  # it also asks for something
    options = _REPLIES[kinds[0]].get(lang, _REPLIES[kinds[0]]["en"])
    return options[next(_counter) % len(options)]
