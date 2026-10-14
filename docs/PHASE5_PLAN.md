# Phase 5 plan: text chat with Gemini

Status: **approved 2026-10-14; steps 1 to 5 implemented** (sections 11 and 12). Steps 6 and 7 are not. Statements about Gemini's API and quotas come from a web search
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
| session type and state | `SessionState` | practice / qualify / race / hotlap, phase, time left |
| tyre state | latest snapshot + tracker | core temperatures, pressures, wear, per wheel; only verified fields |
| brake state | latest snapshot | pedal and, **only when the sensor moves**, temperatures (flat sensors are reported as no reliable data) |
| best lap and delta | `LapTracker` | best lap, last lap, delta to the best, live delta |
| penalties | `snap.penalty_time` | unit still to be confirmed live: said with that caveat until then |

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
| 1 | `pit-wall llm-check`: lists models for the key, 3 calls, prints what it learned (latency, 429 hint) | **done against simulated answers**; pending: the author's key and the numbers in `docs/LLM.md` |
| 2 | `GeminiProvider` + HTTP fakes | **done against simulated answers** (tests/test_gemini.py); pending: the real call |
| 3 | `ContextBuilder` + golden tests | **done** (chat/context.py): built on a real race recording, under 1 500 tokens |
| 4 | `IntentRouter` + local answers | **done** (chat/local.py): table of questions passes; every local answer passes its own grounding check |
| 5 | `GroundingCheck` | **done** (chat/grounding.py): formats, roundings, wrong wheel / corner / lap / unit are caught in tests |
| 6 | `ChatSession` + `pit-wall chat` / `pit-wall ask` (live or `--file`) | end-to-end on a replayed race |
| 7 | evaluation set, latency report, docs (README ES/EN, `docs/CHAT.md`) | numbers reported to the author |

## 9. Risks

| Risk | Mitigation |
|---|---|
| Capacity: a model answers 503 "high demand" or times out (seen on the first real run) | `llm.fallback_models`, short timeout, local answers for the frequent questions, cache; never block the loop |
| Thinking models spend the output budget before writing text (seen: MAX_TOKENS at 16 tokens) | budget sized for thinking, `llm.thinking_budget`, error that says where the tokens went |
| A listed model is retired for new users (seen: 404 on the old default) | the API's own message and its suggested replacement are shown by `llm-check` |
| Free quotas unknown or changing | adaptive limiter, daily budget, `llm-check`, local answers for the frequent questions |
| Model name or endpoint drift | model in YAML, `llm-check` lists what exists; the provider interface isolates it |
| Invented or misread numbers | grounding check, short answers, evaluation set, numbers labelled with units in the context |
| Latency above 3 s | local answers, cache, small context, no streaming needed for text; reported honestly |
| Data used by the provider | consent flag off by default, minimal context, README states it |
| Key leakage | `.env` ignored, redaction in logs and exceptions, test that the key never appears in output |
| Scope creep into strategy/setup | the system prompt and the router refuse and point to the later phase |
| Language mixing (es/en) | one configured language; the prompt fixes it; tests in both |
| Context stale during a lap | timestamp in the context; the answer says "hace N s" when the data is older than 5 s |

## 10. Decisions (2026-10-14)

1. **Transport:** plain HTTPS, no new dependency; the provider stays behind `LLMProvider` so it can be swapped.
2. **Consent:** `llm.allow_cloud` is `false` by default and **the chat says so clearly** when it is off (it
   works with the local answers and tells the user how to turn the cloud model on).
3. **First deliverable:** terminal chat only, no voice.
4. **Key:** the author puts it in `.env` the same evening; until then everything runs on simulated answers.
5. **Local answers:** the table of section 3, which must include tyre and brake state, session type, best lap
   and delta and penalties (added).
6. **Language:** Spanish by default, configurable.

### Conditions that bind the steps below

* **Grounding accepts equivalent formats and roundings** (`1,9` = `1.9`, `1.90`, "1 coma 9"; a value rounded to
  the precision the context carries) **and is tested on a correct number attached to the wrong place**: the
  check is necessary, not sufficient (section 4), so there are explicit cases where the number exists in the
  context but belongs to another corner, wheel or lap, and the evaluation set measures how often the model
  does it.
* **The evaluation set includes questions the context cannot answer** (weather, rivals' state, anything not
  in the summary): the correct answer is "no tengo ese dato". It is reported separately because it is where
  models fail most.
* **The model name lives only in the YAML.** If it does not exist the error says which models the key accepts
  (implemented: see section 11).

## 11. Status of steps 1 and 2

**Implemented** (simulated answers only; **nothing has been exercised against the real service**):

* `engineer/gemini.py`: `GeminiProvider` over `urllib`, an injectable `HttpTransport`, `list_models`,
  `generateContent` request/response mapping, 429 with `Retry-After` or `RetryInfo`, key rejection, safety
  blocks, empty answers, network errors; the key goes in a header and is redacted from every error text.
* The model name is read only from `llm.model`; a test fails if a model name appears in source other than
  `config.py`. A 404 triggers a model listing and the error names the models the key can use.
* `llm.allow_cloud` (default false) is enforced inside the provider; the message explains what would be sent
  and that the free tier may use it.
* `pit-wall llm-check [--calls N] [--allow-cloud]`: models, latency, tokens, 429 hints; sends only the
  one-line prompt "Reply with the single word: ok".
* The limiter now uses the client's clock (the 429 cool-down is testable and consistent).

**To verify with the key** (all marked in `gemini.py`): request and response field names, the model list
endpoint and its pagination, the exact 429 body, the name of the default model in the YAML, and the free-tier
numbers.

## 12. Status of steps 3, 4 and 5

All three work **without a network** and are covered by `tests/test_chat.py` (about 70 cases).

* **Context** (`chat/context.py`, `chat/facts.py`): the compact JSON is built from a *fact sheet* in which each
  number keeps its identity (a wheel, a corner, a lap, a unit). Fields without a reliable meaning are left
  out: a flat brake-temperature sensor becomes a note, an unverified penalty unit is flagged. The braking
  analysis of the last lap against the best valid lap is included when the track geometry is available, and
  says why when it is not.
* **Local answers** (`chat/local.py`): fuel and laps left, last/best lap and delta, tyres, brakes, session
  type and position, penalties, where time is lost, which corner is that (name, number, ordinal, ambiguity
  asks back), what the last alert was, and data that can never be in the context (weather, rivals) answered
  as "no tengo ese dato" without spending quota. Answers are written from the same fact sheet the model would
  receive.
* **Grounding** (`chat/grounding.py`): accepts `1,9` / `1.9` / `1 coma 9`, roundings, `1:24,9`, seconds and
  milliseconds, the missing sign of a delta; flags a number that is in no fact (`unsupported`), a correct
  number attached to another wheel, corner or lap (`misattributed`, resolved by the nearest mention in the
  sentence) and a correct number with the unit of another quantity (`unit`). `sanitize` drops only the
  offending sentences and refuses honestly if nothing is left.

**Known limits**

* Words are not checked, only digits ("casi dos vueltas" is the local layer's own phrasing).
* The nearest-mention rule can be fooled by a sentence that names a subject far from its number; the
  evaluation set (step 7) measures that on the real model.
* The router is keyword based: a question phrased unusually goes to the model, which is the safe side.
* Not done yet: the chat session itself (history, the call to the model with this context and the grounding
  check on its reply, the notice when `allow_cloud` is off) and the evaluation set: steps 6 and 7.

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
