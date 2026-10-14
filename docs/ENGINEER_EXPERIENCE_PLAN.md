# The engineer as a character: radio protocol, persona, events, memory and settings (plan)

Status: **plan only**, written 2026-10-15 from the author's list. Nothing here is implemented except the
`acknowledge` action. Each item says what it needs, whether the data exists today, and what is unknown. The
words *verified* and *to verify* mean what they mean in the README table: nothing is "verified" without
evidence from a real capture.

*Resumen en español al final.*

## 1. The radio is half-duplex (this answers the open question of phase 6)

The F1 radio is one channel: one party transmits at a time, the engineer waits for a straight and keeps it
short, and the driver acknowledges (a word or the ACK button). The project already does the first half
(phase 3: calm moments, critical interrupts). Phase 6 adopts the rest as a **channel model**:

```
IDLE  --engineer starts-->  ENGINEER_TX  --done-->  IDLE
IDLE  --driver presses PTT--> DRIVER_TX  --release--> (transcribe, answer) --> ENGINEER_TX or IDLE
```

| Situation | Rule |
|---|---|
| the driver holds PTT | the engineer **does not start** a message (non-critical alerts wait) |
| the driver presses PTT while the engineer talks | by default the **driver wins**: the engineer is cut at once (the driver is the one at the wheel); the cut message is **re-queued** when it is still relevant (not past its TTL) and said after the exchange. Configurable: `radio.driver_wins: true | false` |
| a **critical** alert (spin, puncture, fuel) while anyone talks | goes out anyway: safety first |
| the engineer asks for a confirmation ("confirma si puedes seguir así") | the engineer waits for **ACK** (tap on `acknowledge`) or a spoken yes/no; no answer in N seconds: it asks once more or notes "sin confirmación" |
| long answers | split into short messages, as an engineer would |

**Immediate acknowledgement** (what Bozzi's "we are checking" is): on releasing PTT the engineer says a short
persona phrase at once ("Copiado, miramos") while the answer is computed. It hides the recognition and model
latency (the answer is the part that takes 1 to 4 s) and is the right place for the persona's catchphrase.

Action added now: `acknowledge` (a tap action in `input/actions.py`), the wheel's ACK button.

## 2. Personality: profiles on top of fixed rules

A profile is **layers**, from the immovable to the personal:

| Layer | What | Who controls it |
|---|---|---|
| 1. Core rules | numbers only from the context, say "no tengo ese dato", no invented weather or rivals, units, brevity | the project; **a profile cannot change it** (the grounding check enforces it anyway) |
| 2. Persona | tone, formality, humour, catchphrases, how it greets and signs off, the acknowledgement phrase, reaction to the driver's mood | the profile (a prompt, in the user's own words) |
| 3. Names | the driver's name and the engineer's name, said in alerts and answers | settings |
| 4. Talkativeness | what is announced unprompted (section 4) | settings |

```yaml
# profiles/sobrio.yaml  (shipped presets live in the repo; the user's own in a git-ignored folder)
name: Sobrio
description: Serious, concise, no small talk.
engineer_name: Marco          # overridable in settings
language: es
acknowledge: ["Copiado.", "Recibido, miramos."]
persona_prompt: |
  Hablas como un ingeniero de pista serio y conciso. Frases cortas, sin adornos, sin exclamaciones.
  Antes de una respuesta que requiere cálculo puedes decir una frase corta de acuse.
alert_style: terse            # which phrase pack the rule-based alerts use
```

* **Two surfaces, two mechanisms.** *Chat answers* are written by the model, so the persona prompt applies.
  *Alerts* are instant, local and rule-based (latency under 200 ms, no network): they use **phrase packs**
  (a few variants per alert per style, chosen deterministically), not a model call.
* **Mood and humour** (the Lambiase kind of reaction): it is an instruction in the persona prompt ("si el piloto
  bromea, responde con humor seco; si está tenso, sé más breve"), judged by the model from the driver's words.
  It cannot see a face or a voice tone: only the text of what was said.
* **Presets**: shipped as *descriptive styles* ("el estilo sobrio y de datos", "el que dice «estamos revisando»
  antes de responder", "el de humor seco que reacciona al ánimo del piloto"), not impersonations of named
  people, and without reproducing real radio conversations. The author can write a profile named after
  anyone for personal use; that is a file on their own PC.
* **Own profiles**: a folder of YAML files, selectable by name (config, command line, and later the settings
  window); a "test this profile" action that runs a fixed set of questions and shows the answers, to judge a
  persona before a race.
* **Risk**: a persona prompt that fights the core rules (making it chatty, or confident about what it does not
  know). The core layer comes last in the prompt and the grounding check still removes unsupported numbers;
  the evaluation set is run per profile.

## 3. Events the engineer should notice by itself

| Event and example | Data | State today |
|---|---|---|
| **Flags**: yellow ("bandera amarilla, con cuidado"), blue ("deja pasar a <Nombre>"), white (last lap), black, penalty, checkered | `graphics.flag` in AC (a small integer; the code table is **to verify**); iRacing has more (white, meatball: go to the pits for repairs) | a rule exists, **disabled**: no flag has ever been captured here. Native AC **penalty** (black flag) comes in two kinds the author listed: *track limits* (slow down for X time, or reach X speed) and *jump start* (drive-through); the white flag (last lap / slow car ahead). The fields and codes are **to verify** with captures; nothing is announced until then |
| **Damage**: "daños leves en la suspensión: confírmame si puedes seguir" | `car_damage`, five body zones in the physics page | rule exists, **disabled**, never captured with damage; AC's page has body zones, a *suspension* value is **not known to exist** |
| **Safety car / virtual safety car** | not in AC's shared memory. Real Penalty (installed) has its own safety-car and VSC logic and its own channel (UDP to its server, on-screen important messages, a log per session) | **unknown**: needs a captured event; options: read its log, or an in-game bridge app |
| **Overtakes and gaps**: "buen adelantamiento, a 1,8 s está <Nombre>", "estamos primeros" | own `position` exists; the car ahead, its name and the gap need the **rivals feed** | own position: available; rivals: blocked on the phase 8b spike |
| **Blue flag with a name** | the flag plus knowing *which* car is behind | blocked on the rivals feed |
| **Penalty** | `penalty_time`, unit **unverified** | rule exists |
| **Last lap, race end** | laps and time left; "has extra lap" in static | to verify with a race with a lap or time limit |

**Talkativeness** is a setting, because "no siempre se dice, para no distraer": every event has a level
(`critical`, `important`, `info`) and the profile of the author chooses what is volunteered. Defaults:
critical and important are said; *info* (overtakes, position, gaps) is said **only when asked** ("¿en qué
posición voy?", "¿a cuánto está el de delante?"), unless the user raises the level.

## 4. Conversation memory

"¿Qué me habías dicho de la curva 4?" is a retrieval question, not a model question:

* every message the engineer says and every question asked is recorded with its **lap, corner, rule and
  values** (the engineer already remembers recent alerts; this widens it and persists it for the session);
* the answer is built from those records ("en la vuelta 5 te avisé: frenas 14 m antes que tu mejor vuelta y
  pierdes 2 décimas en la curva 4") and the model only phrases it;
* across sessions, the ledger of phase 8 (what was suggested, what happened).

"¿En qué curvas debería ahorrar gasolina y cómo?" is **not** answerable from today's data: it needs the fuel
used per stretch (the fuel level resolution is to be measured), the throttle and braking profile, and a
validated lift-and-coast estimate. It belongs to phase 8 (strategy), with the validation that a suggestion
saved fuel and what it cost in time.

## 5. A settings interface

Everything configurable today is a YAML key; the author wants it set in an interface. Proposal:

* a **local web page** served on `127.0.0.1` by the application (the same code later runs inside the `.exe`),
  writing the same YAML: command line users are unaffected;
* sections: *Driver and engineer* (names, language, profile), *Voice* (microphone and output device by name,
  push-to-talk and the other buttons, a test of each), *Recognition* (CPU / GPU / cloud, model size, threads),
  *Model provider* (provider, key in the credential store, model list from `list_models()`, a test button,
  consent), *Talkativeness*, *Alerts* (thresholds with their defaults and notes), *Simulators* (a profile per
  sim);
* every setting has a **Test** where one makes sense (record 3 s and see the level; say a phrase on the chosen
  device; run `llm-check`).

## 6. Settings per simulator

A profile per simulator (`profiles.sim.ac`, `profiles.sim.iracing`...) that overrides the general settings:
recognition on the CPU for one game and on the GPU for another, a different phrase pack, a different set of
flags. Selection is by the **detected** simulator (`docs/MULTI_SIM_PLAN.md`), or forced by `--sim`. Until more
than one simulator exists, the structure is there and the only profile is AC.

## 7. Where each piece goes

| Piece | Phase | Depends on |
|---|---|---|
| half-duplex channel, acknowledgement phrase, ACK button, names, device selection | **6** (voice) | steps 2 to 4 of phase 6 |
| profiles, phrase packs, persona prompt, "test this profile" | **6b** (right after voice) | the chat (done) and the voice output |
| own position, position/gap questions | 6b | none |
| conversation memory ("¿qué me dijiste de la curva 4?") | 6b | alert records (exist) |
| settings web page | **11**, with a CLI wizard before | the settings above |
| flags, damage | **10** | **captures** of each (author), then enabling the rules |
| safety car, penalties from Real Penalty | 10 | a captured event and a decision on log vs bridge |
| overtakes, gaps, names, blue flag with a name | 10 | the rivals feed (phase 8b) |
| fuel saving per corner | 8 | strategy ledger and validation |
| profile per simulator | 9 (multi-sim) | detection |

## 8. What is needed from the author

1. **Captures** (not code): one session with each **flag** that can be provoked (yellow and blue against AI
   cars; a penalty or black flag if possible), one with a **contact that causes damage**, and one on a
   **Real Penalty** setup with a safety car or a penalty; plus that session's Real Penalty log. These are
   recorded with `pit-wall record` and added to `docs/PENDIENTES.md`.
2. **Profiles**: the author said they can generate prompts: the format of section 2 is the place to put them.
3. **Decisions**: the default of "driver wins on PTT" (section 1) and which events are volunteered by default.

---

## Languages (2026-10-14): es, en, de

`engineer.language: es | en | de` selects the text AND the voice language. German covers: the spoken alerts
(`messages.py`), the model instructions and the grounding refusal, the persona styles, the spoken orders
("nicht stören", "du kannst wieder sprechen"), the radio phrases, the memory ("was hast du mir über Kurve 4
gesagt"), the recogniser vocabulary and language, the corner aliases ("Kurve 4", "Kurve vier") and the
decimal comma, and (2026-10-14) the **local answers** (`chat/local_de.py`: fuel, tyres, brakes, laps,
session, penalties, time left, loss by corner, which corner is which, last alert, "not yet", "no data"),
written from the same facts and passing the same grounding check, so German works offline too. Pending for
German: a German Windows voice (none is installed on the author's PC; `pit-wall voices` lists them once
added) and measuring the recognition with real German phrases.

## Status of phase 6b (2026-10-14)

* **Persona** (`engineer/persona.py`, `engineer.persona` in the config): engineer name, driver name, a style
  among `neutral | brief | calm | technical | warm | direct` and free notes (`custom`). It only adds lines to
  the model's instructions and to the voice greeting; numbers, rules and the grounding check are untouched.
  Styles describe a manner; none imitates a real person. Local answers are not restyled (they are built from
  facts), so the style shows in the model's answers.
* **Memory** (`chat/recall.py`): "¿qué me habías dicho de la curva 4?" quotes the alerts and the earlier
  answers that mention that corner (most recent first, with how long ago). A quote is not re-checked against
  the current facts: it is what was said. Without a corner, "qué me dijiste" is still the last-alert
  explanation. Not done yet: recall by topic ("qué me dijiste de la gasolina"), and persistence across sessions.

## Resumen en español

* **La radio es semidúplex**: uno habla a la vez. El ingeniero no empieza mientras mantienes el botón; si lo
  pulsas mientras habla, **ganas tú** (se corta y, si sigue siendo útil, repite después); un aviso crítico
  entra siempre. Un botón **ACK** confirma sin hablar. Al soltar el botón dice una frase corta de acuse
  ("Copiado, miramos"), que además esconde la latencia del modelo.
* **Personalidad por capas**: reglas fijas (los números salen del contexto) + tu perfil (tono, humor,
  muletillas, cómo reacciona a tu ánimo) + nombres + cuánto habla. Los **avisos** instantáneos usan paquetes de
  frases por estilo (no llaman al modelo); las **respuestas del chat** usan el prompt de personalidad. Los
  presets son estilos descriptivos, sin imitar a personas reales ni copiar sus conversaciones; tú puedes crear
  los perfiles que quieras en archivos YAML.
* **Eventos**: banderas, daños, safety car, adelantamientos. Hoy **ninguno está verificado**: las reglas de
  banderas y daños existen pero apagadas porque nunca se capturó uno. El safety car **no** está en la memoria
  compartida de AC (Real Penalty tiene su propio canal). Adelantamientos, huecos y "deja pasar a <Nombre>"
  dependen de los datos de rivales (fase 8b). Posición propia sí está disponible.
* **Memoria de conversación**: "¿qué me dijiste de la curva 4?" es una búsqueda en lo que ya dije, no una
  pregunta al modelo. "¿Dónde ahorrar gasolina y cómo?" necesita análisis nuevo (fase 8).
* **Interfaz de ajustes**: una página web local que escribe el mismo YAML (luego dentro del `.exe`), con botón
  de prueba en cada ajuste y perfiles por simulador.
* **Lo que necesito de ti**: capturas de cada bandera, de un daño y de un safety car/penalización con Real
  Penalty (más su log), tus perfiles, y dos decisiones por defecto.
