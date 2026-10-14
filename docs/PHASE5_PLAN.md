# Phase 5 plan: text chat with Gemini

Status: **plan, waiting for approval** (rule of the project: plan and risks first, then implementation).
Nothing in this file is implemented. Statements about Gemini's API and quotas come from a web search
(2026-10-14) and from the author's earlier notes; they are marked *to verify* and are checked by the
`llm-check` command of step 1 before anything depends on them.

*Resumen en español al final.*

## 0. Goal and non-goals

**Goal.** From a terminal, ask the engineer things about the session in natural language and get a short,
correct answer in under 3 s, built from a compact summary of the live (or replayed) telemetry:

* "¿cómo van mis neumáticos?", "¿cuántas vueltas de gasolina tengo?", "¿dónde pierdo más tiempo?",
  "¿cuál es esa curva?", "explícame el aviso de antes".

**Non-goals (later phases).** Voice in or out (phase 6), post-session debrief (7), strategy and setup advice
(8). The model **never** starts a conversation and never touches the rule-based alerts: those keep working
with no network, exactly as in phase 3. In phase 5 the model has no tools and cannot change anything.

## 1. Principles carried over

1. **Numbers from code, words from the model.** Every number in an answer must exist in the context that
   was handed over (or be derivable by the code); a check enforces it (section 4).
2. **Cheap things never go to the model.** Facts the code already knows are answered locally, which also
   saves quota and latency (section 3).
3. **Degrade, never fail.** No key, no network, 429, daily budget: the user still gets the local answer
   or a clear "I cannot reach the model; here is what I know".
4. **Provider behind an interface.** Gemini is one implementation of the existing `LLMProvider`; the
   `mock` provider stays the default and is what the tests use.
5. **Nothing leaves the machine without consent** (section 6).

## 2. Architecture

```
terminal / later voice -> ChatSession -> IntentRouter -> LocalAnswers  (facts, corner glossary)   [no model]
                                              |
                                              v
                                       ContextBuilder -> LLMClient (limiter + cache) -> Provider (gemini | mock)
                                              |                                              |
                                              +--------------- GroundingCheck <--------------+
```

* **`ContextBuilder`** produces the compact JSON summary from `SessionEngine`, `LapTracker`, the last
  alerts and the braking analysis. Budget: about 1 500 tokens, built in under 5 ms, with a timestamp.
  Only fields whose unit status is *verified* or *plausible* in the README table are included; *unreliable*
  or *undetermined* ones are omitted (the model must not reason about brake temperature on a car whose
  sensor is flat).
* **`IntentRouter`** is deterministic (keywords + `find_corner`): it picks a local answer when it can.
  Everything else goes to the model.
* **`GeminiProvider`** implements `LLMProvider.generate`. *To verify at the spike*: the model name in
  `config/default.yaml` (`gemini-2.5-flash`) may be outdated; the `llm-check` command lists the models the
  key can use.
* **`GroundingCheck`** compares the numbers in the reply with the numbers in the context.
* **`ChatSession`** keeps the last N turns (default 6), the system prompt and the language.

### 2.1 Transport

Plain HTTPS to the `generateContent` endpoint (standard library, no new dependency) rather than the
`google-genai` SDK: full control of timeouts and of how a 429 and its retry hint are read, one fewer
dependency to install, and the `LLMProvider` interface already isolates the rest. The SDK stays an option
behind the same interface. *Both to verify against the current API reference during the spike.*

## 3. Local answers (no model)

| Question | Source | Notes |
|---|---|---|
| fuel left in laps / litres | `FuelRule.per_lap`, `snap.fuel` | same wording as the alerts ("casi dos vueltas") |
| last lap, best lap, delta | `LapTracker` | |
| where am I losing time | `braking.worst_issue` of the last lap | corner name, braking-point difference, time |
| which corner is that | `find_corner` + `describe_corner` | "Prima Esse, que es la curva 11, la última" |
| current tyre temperatures/pressures | latest snapshot | per wheel, with the unit |
| what was that alert | alert log (rule + values) | says the measured numbers, not a story |

Everything else is an open question for the model, with the context attached.

## 4. Grounding: not letting it invent numbers

* The system prompt tells the model to use only the numbers in `context`, to say "no tengo ese dato"
  otherwise, to answer in at most two short sentences, in the configured language, in radio style.
* After the call, every number in the reply is checked against the numbers of the context (with unit
  conversions the code knows, e.g. laps <-> litres). A reply with a number that is not supported is **not
  shown as is**: it is replaced by the local answer if one exists, or the number is dropped and the sentence
  is marked as unverified. Each violation is logged with the reply and the context hash.
* The check is a safety net, not a guarantee: it cannot judge a wrong *interpretation* of a correct number.
  That risk is handled by keeping the answers short and by the evaluation set (section 7).

## 5. Free tier: throttling, cache, degradation

* **Adaptive limiter** (exists): starts at `initial_rpm`, halves on 429, honours the retry hint, creeps up
  on success, optional daily budget. Chat requests are user-initiated, so they use the `urgent` lane, but
  the limiter still protects against a loop.
* **Cache** (exists): identical question and context within `cache_ttl_s` return the stored answer.
  The context is rounded (laps to 0.1, temperatures to 1 degree) so that a repeated question hits the cache.
* **Quotas are not public.** The search found that Google no longer lists per-model free-tier numbers on
  its public page; they are shown per project in AI Studio, and an older table listed 10 RPM and 250 requests
  per day for a Flash model. *To verify:* the author reads the AI Studio quota page, and `llm-check` measures
  what the key does in a short burst.
* **Degradation ladder:** cache -> local answer -> model -> "sin conexión con el modelo; esto es lo que sé:"
  + the deterministic summary. The state of the ladder is shown in the prompt line (`[local]`, `[cache]`,
  `[gemini 1.2 s]`, `[limited: retry in 40 s]`).

## 6. Privacy and consent

* What is sent: the compact context and the question. Never the raw telemetry, never the player's name
  (already stripped from recordings), no rival names (no rival data exists in phase 5).
* **The free tier may use inputs to improve Google's products** (reported on Google's pricing page).
  Therefore `llm.allow_cloud` defaults to `false`: with it off, only the `mock` provider and the local answers
  work, and the first time the user turns it on the tool prints what is sent and where.
* The API key lives in `.env` (git-ignored); it is never logged, printed or included in an exception text.

## 7. Tests and evaluation

* Provider contract tests against `mock`; an HTTP-level fake for `GeminiProvider` (200, 429 with and
  without a retry hint, 500, timeout, malformed body, safety block).
* Context builder: golden JSON on the recorded Magione sessions; size under the budget; unreliable fields
  absent.
* Router: a table of questions (es/en) to expected intent; ambiguity ("la esse") asks back.
* Grounding: replies with an unsupported number are replaced; supported ones pass; unit conversions.
* Degradation: no key, `allow_cloud: false`, 429 loop, daily budget reached.
* **Evaluation set** (`tests/eval/`): 30-40 questions with a recorded context and the facts that a correct
  answer must contain; run on demand with a real key (`-m real_llm`), reported with pass rate and latency,
  never part of the normal run.
* **Latency report**, like phase 3: local answers (p50/p95), cache hit, model p50/p95 on the author's line.

## 8. Work breakdown

| Step | Content | Done when |
|---|---|---|
| 1 | `pit-wall llm-check`: lists models for the key, 3 calls, prints what it learned (latency, 429 hint) | the author's key works and the numbers are in `docs/LLM.md` |
| 2 | `GeminiProvider` + HTTP fakes | contract tests pass; real call works |
| 3 | `ContextBuilder` + golden tests | size and content verified on real sessions |
| 4 | `IntentRouter` + local answers | table of questions passes; latency under 50 ms |
| 5 | `GroundingCheck` | violations are caught in tests |
| 6 | `ChatSession` + `pit-wall chat` / `pit-wall ask` (live or `--file`) | end-to-end on a replayed race |
| 7 | evaluation set, latency report, docs (README ES/EN, `docs/CHAT.md`) | numbers reported to the author |

## 9. Risks

| Risk | Mitigation |
|---|---|
| Free quotas unknown or changing | adaptive limiter, daily budget, `llm-check`, local answers for the frequent questions |
| Model name or endpoint drift | model in YAML, `llm-check` lists what exists; the provider interface isolates it |
| Invented or misread numbers | grounding check, short answers, evaluation set, numbers labelled with units in the context |
| Latency above 3 s | local answers, cache, small context, no streaming needed for text; reported honestly |
| Data used by the provider | consent flag off by default, minimal context, README states it |
| Key leakage | `.env` ignored, redaction in logs and exceptions, test that the key never appears in output |
| Scope creep into strategy/setup | the system prompt and the router refuse and point to the later phase |
| Language mixing (es/en) | one configured language; the prompt fixes it; tests in both |
| Context stale during a lap | timestamp in the context; the answer says "hace N s" when the data is older than 5 s |

## 10. Decisions needed from the author

1. **Transport:** plain HTTPS (recommended) or the `google-genai` SDK.
2. **Consent:** `llm.allow_cloud: false` by default, as proposed?
3. **First deliverable:** terminal chat only (voice is phase 6). Agreed?
4. **A Gemini API key** in `.env` (free, from AI Studio), and a look at the AI Studio quota page so the
   numbers can be written down (or paste what it shows).
5. **The local-answers table** of section 3: anything to add or remove?
6. **Default language** of the chat: Spanish (as the alerts)?

---

## Resumen en español

**Qué**: un chat de texto en terminal donde preguntas por la sesión ("¿cuántas vueltas de gasolina me
quedan?", "¿dónde pierdo tiempo?", "¿cuál es esa curva?") y contesta en menos de 3 s con un resumen compacto
de la telemetría. Sin voz (Fase 6) y sin estrategia ni setup (Fase 8); el modelo nunca inicia nada y las
alertas por reglas siguen funcionando sin red.

**Cómo**: lo que el código ya sabe (gasolina, vueltas, curva con más pérdida, glosario de curvas) se responde
**sin modelo**; el resto va a Gemini con un contexto de ~1 500 tokens solo con campos verificados. Una
comprobación impide mostrar números que no estén en el contexto. Limitador adaptativo, caché y degradación
escalonada para el plan gratuito, con cuota desconocida que se mide con `pit-wall llm-check`.

**Privacidad**: el plan gratuito puede usar lo enviado para mejorar productos de Google, así que
`llm.allow_cloud` está desactivado por defecto y se explica qué se envía antes de activarlo.

**Necesito**: tu clave de Gemini en `.env`, mirar la página de cuotas de AI Studio, y tu OK a las seis
decisiones de la sección 10.
