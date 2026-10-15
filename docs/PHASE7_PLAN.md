# Phase 7: the post-session debrief

Status: **proposal, waiting for the author's approval. No code yet.**

## 0. Goal

When a session ends, the engineer gives a **debrief**: what happened, where the time went, what to work on, and
how it compares with the last time on this track. A written report is kept; a short spoken version is read.

Same rules as the rest of the project: **every number comes from code** (the laps, the braking analysis, the
tyre index, the memory of earlier sessions); the model only phrases, its sentences go through the same
grounding check, and without the model a local template writes a plainer version. It is the "automated race
debrief" idea of the author's roadmap, built on what already exists.

Not in scope: setup advice (8c), rivals (8b), race strategy (8a, 8d).

## 1. What the debrief says (all computed)

| Block | Content | From |
|---|---|---|
| Result | session kind, track, car, laps, position, start to finish (races), grid (qualifying) | `SessionState`, milestones |
| Pace | best lap, the **ideal lap** (sum of the best sectors), the gap between them, consistency (spread of the valid laps) | `LapTracker` |
| Stints | laps per set of tyres, pace trend per lap (incident laps left out), fuel per lap and fuel used | laps, existing trend |
| Where the time went | the 3 corners that cost most against the best lap, with the measured braking-point difference | `analysis/braking.py` |
| Tyres | where they were worked hardest (an **estimate**, said as such), front against rear, temperature range | `analysis/tyre_corners.py` |
| Incidents | spins, off-tracks, saved slides, laps excluded and why | incident detection |
| Penalties and flags | what the game reported (when the rules are enabled with real captures) | rules |
| Against last time | best-lap difference and consistency against the previous debrief on this track and car | the sessions file (section 3) |

Every block says its **sample size** ("sobre 9 vueltas válidas") and is left out when there is not enough
data, instead of guessing.

## 2. Output

* **Markdown report** in `~/.pit-wall-ai/debriefs/<date>_<track>_<car>.md` (a local folder; the path is a
  setting); the same text is printed by `pit-wall debrief FILE` for a recording.
* **Spoken summary**: 3 to 4 sentences, under 25 seconds, through the voice, in the language of the
  engineer and with the persona's style. The rest is available on request ("dame el detalle de los
  neumáticos").
* **A facts block** (`debrief.json`) next to the report: the numbers the sentences were checked against.

## 3. Memory between sessions

A small `sessions.jsonl` (next to the alert memory) keeps one summary line per session: track, car, kind,
date, best lap, ideal lap, consistency, laps, tyres. It is what "against last time" reads. Local, deletable,
opt-out like the alert memory.

## 4. When it triggers

| Case | Trigger |
|---|---|
| Race by laps | the finish (already detected) |
| Qualifying | the session ends (already detected) |
| Practice / timed races | the driver asks ("dame el resumen de la sesión"), or the game session ends, or the program is stopped |
| Any recording | `pit-wall debrief FILE` |

Quiet mode ("no molestes") postpones the spoken part; the written one is always produced.

## 5. Steps (each waits for approval)

| Step | Content | Done when |
|---|---|---|
| 1 | `pit-wall debrief FILE`: the facts and the Markdown report from a recording, no model | correct on the three recordings of the author, numbers checked by hand against the lap list |
| 2 | spoken summary with the model, grounding check, a local fallback; evaluation cases | the cases pass on the author's recordings; nothing ungrounded is said |
| 3 | live trigger, the sessions file, "against last time" | end to end on a real session pair |
| 4 | README ES/EN, `docs/VOICE.md` section, pending list | |

## 6. Risks

* **Where a practice session ends** is not in the shared memory: on demand and on exit are the honest
  triggers; the debrief must not fire in the middle of a session.
* **Small samples**: 3 clean laps do not make a consistency figure. Blocks state their `n` and are dropped
  below a minimum.
* **Too much talk**: the spoken version is the 3 to 4 most important facts, chosen by size of effect, not a
  recital of the report.
* **Misleading comparisons**: another car or another compound is not "last time": the comparison needs the
  same track and car (and says if the conditions differ).
* **Estimates dressed as facts**: the tyre block carries the estimate label everywhere it appears.

## 7. Questions for the author

1. Spoken summary by default after a race, or only the written one until you ask?
2. For practice sessions: is "on demand or when the game session ends" enough?
3. Report language: always the engineer's, or the README's English for sharing?
4. Do you want the report to include a small chart (lap times, a corner map)? It is cheap to add, as an
   HTML file next to the Markdown.

---

## Resumen en español

Al terminar una sesión el ingeniero da un **resumen**: qué pasó, dónde se fue el tiempo, qué trabajar y cómo
se compara con la última vez en esa pista. Se guarda un informe escrito y se lee una versión corta en voz.
Todo número sale del código (vueltas, análisis de frenadas, indicador de neumáticos, memoria de sesiones
anteriores); el modelo solo redacta y pasa por la misma comprobación de cifras. Cada bloque dice con cuántas
vueltas se calculó y se omite si no hay datos suficientes. Pasos: 1) informe desde una grabación, sin modelo;
2) resumen hablado con modelo, comprobación y casos de evaluación; 3) disparo en vivo y comparación con la
sesión anterior; 4) documentación. Riesgo principal: la práctica no tiene un final detectable, así que se
pide a demanda o al cerrar. Necesito tu visto bueno y las cuatro respuestas de la sección 7.
