# The language model (phase 5)

Status: provider and `llm-check` implemented and tested against **simulated** answers. Nothing has been run
against the real Gemini API yet; the table at the end is filled in after `pit-wall llm-check` with a real key.

## Turning it on

1. Create a free key in Google AI Studio and put it in `.env`: `GEMINI_API_KEY=...` (git-ignored).
2. In `config/local.yaml`:

   ```yaml
   llm:
     provider: gemini
     allow_cloud: true      # nothing is sent unless this is true
     model: <a model name that llm-check lists for your key>
   ```

3. Check it: `pit-wall llm-check` (or `pit-wall llm-check --allow-cloud` for one run without editing the YAML).

## What is sent

The question and a compact summary of the session. Never raw telemetry, never the player name. The test
call of `llm-check` sends only the line "Reply with the single word: ok". **The free tier may use what is
sent to improve Google products**; that is why the cloud model is off by default.

## If the model name is wrong

`llm-check` (and the chat) list the models your key can use and tell you to set `llm.model`. The model name
exists only in the YAML.

## Measured on the author connection (2026-10-14, other PC, free tier)

| Item | Value |
|---|---|
| models available to the key with `generateContent` | 46 (includes image, speech, music, robotics and agent models: not all are for chat) |
| `gemini-2.5-flash` (old default) | listed, **404: no longer available to new users** |
| `gemini-3.8-flash` (the model the API suggested) | answers, but **503 "high demand" on 3 of 3 calls** |
| `gemini-3.5-flash` with `maxOutputTokens: 16` | **no text, finish reason MAX_TOKENS** on 3 of 3 (thinking tokens count in the budget) |
| `gemini-3.1-flash-lite` | **works**: 3125 ms, 890 ms, then a read timeout at 8 s; 21 tokens per tiny call |
| quota seen in AI Studio | _pending: the author reads it from the AI Studio rate-limit page_ |
| 429 behaviour (retry hint) | _not seen yet_ |

Reading: with n = 2 successful calls nothing is a latency figure yet, but it is enough to say the free tier
is **not reliably under 3 s**: one call took 3.1 s, one timed out. That is why the chat answers what it can
locally, caches, and has fallback models.

## Found on the first real run (2026-10-14)

* The key, the `models` listing and its pagination work as the code expects (46 models with `generateContent`).
* The default model of the YAML, `gemini-2.5-flash`, **is still listed but answers 404** to `generateContent`:
  *"no longer available to new users. Please update your code to use models/gemini-3.8-flash ... We recommend
  you to use the Interactions API"*. Lesson: a model being in the list does not mean it can be used, and the
  API names its replacement; `llm-check` now shows the API's own message and the model it suggests, and says
  whether that one is in the key's list.
* Google points to an **Interactions API** for new code. Whether `generateContent` stays supported for the
  newer models is *to verify*; the provider interface isolates a future change of endpoint.

## Second real run (2026-10-14): three different failures, three fixes

* **503 "This model is currently experiencing high demand"** on the model the API itself recommended.
  Transient, nothing wrong with the key or the request. Now a distinct `LLMTransient` error; set
  `llm.fallback_models` (names in YAML only) and the provider tries them in order on a 503, 504 or timeout.
* **MAX_TOKENS with no text** at `maxOutputTokens: 16`: the newer models think first and those tokens count in
  the output budget. The test call now uses the configured budget; the error says how many tokens went to
  thinking; `llm.thinking_budget` is sent when set (field name *to verify* per model).
* **Read timeout at 8 s** on a one-word prompt. `llm.timeout_s` stays at 8 for now; the fallback list is what
  keeps a stuck model from blocking an answer.
* The default model is now `gemini-3.1-flash-lite`, the only one that answered in this run. It is a default
  that **worked once**, not a recommendation: `llm-check --model <name>` compares others, and the aliases
  `gemini-flash-latest` / `gemini-flash-lite-latest` in the list would avoid the next retirement (untested).

## Third real run (2026-10-14, same PC, a few minutes later)

| Model | Calls | Result | Latency |
|---|---|---|---|
| `gemini-3.1-flash-lite` (pinned) | 10 | 8 ok, **1 x 503, 1 x read timeout (8 s)** | median 2359 ms, max 3640 ms; 3 of the 8 over 3 s |
| `gemini-flash-lite-latest` (alias) | 5 | 5 ok | 609-687 ms, median 657 ms |
| `gemini-3.5-flash` | 5 | 5 ok; **110-147 thinking tokens for a one-word answer** | 1406-1734 ms, median 1484 ms |

Reading, with the caution that these are 5 to 10 calls each in a few minutes:

* The pinned lite model was the least reliable of the three at that moment; the alias was the fastest and
  the steadiest. Nothing here says why (the alias may resolve to another model, or load may simply differ):
  `llm-check` now prints the concrete model that answered (`answered by:`).
* A thinking model spends more tokens thinking than answering. For short radio-style answers the thinking
  budget should be limited (`llm.thinking_budget`, field name still to verify) and the output budget sized
  for it.
* The default is now the alias with `gemini-3.5-flash` as the reserve. This is a choice from a very small
  sample, not a proven ranking; a longer run at a different hour is needed (`llm-check --calls 20`).
* No 429 appeared in 20 calls: the per-minute limit was not reached by these runs, so it is still unknown.

## Fourth real run (2026-10-14): the rate limit and the thinking budget

| Run | Result |
|---|---|
| default alias `gemini-flash-lite-latest`, 20 calls back to back | **17 ok, then 3 x 429 "retry in 30 s"**; median 750 ms, one call at 3969 ms, one at 1281 ms |
| `gemini-3.5-flash` with `--thinking-budget 0`, 5 calls | 5 ok, **no thinking tokens**, median 891 ms (was 1484 ms with thinking) |

* **The alias resolves to `gemini-3.5-flash-lite`** (reported by the API in `modelVersion`).
* **`gemini-3.5-flash` was answered by `gemini-3.6-flash`**: that name is itself a moving pointer.
* **The 429 came after 17 requests in about 16 s** and carried a 30 s retry hint. That says only that the
  limit is *at least* 17 requests in that span; the real per-minute and per-day numbers are on the AI Studio
  quota page and are still **not recorded here**. The default limiter (6 requests/minute at start, 10 at
  most) stays well below what was accepted.
* **`thinkingConfig.thinkingBudget: 0` is accepted and works** on this model (no 400, thinking gone,
  roughly 40 % faster on a one-word answer). Not yet tried on the alias.
* Two slow calls in 17 (3969 and 1281 ms) confirm that the occasional call exceeds 3 s even when the
  median is 0.75 s: the chat needs the local answers, the cache and a timeout it can live with.

## Fifth real run (2026-10-14): the AI Studio quota page, and a model that rejects the thinking budget

Quotas seen on the AI Studio rate-limit page (free tier, per model, requests per minute / tokens per minute
/ requests per day):

| Model | RPM | TPM | RPD |
|---|---|---|---|
| Gemini 3.5 Flash Lite (what `gemini-flash-lite-latest` resolves to) | 15 | 250 K | 500 |
| Gemini 3.1 Flash Lite | 15 | 250 K | 500 |
| Gemini 3.6 Flash (what `gemini-3.5-flash` resolves to) | 5 | 250 K | 20 |
| Gemini 3.8 / 3.7 / 3 / 2.5 Flash | 5 | 250 K | 20 |
| Gemini 2.5 Flash Lite | 10 | 250 K | 20 |
| Gemma 4 26B / 31B | 30 | 16 K | 14.4 K |
| 2.5 Pro, 3.1 Pro | 0 | 0 | 0 |

* **Limits are per model**: the two Flash-Lite models have independent allowances, which makes one a real
  reserve for the other. `gemini-3.5-flash` (5 per minute, 20 per day) is not.
* **Failed requests count** (3.8 Flash showed 3/20 after three 503s), so retries and hammering cost quota.
* The documented 15 per minute versus 17 accepted in the earlier burst is a small inconsistency not
  explained here (the window is probably not a strict rolling minute).
* The default reserve is therefore `gemini-3.1-flash-lite`, the daily budget default is 400, and a 429 on one
  model makes the provider rest that model for the `Retry-After` and try the reserve at once.
* **The Flash-Lite alias answered HTTP 400 ("Request contains an invalid argument") to
  `thinkingBudget: 0`**, while `gemini-3.5-flash` accepted it. The budget is therefore per model
  (`llm.thinking_budgets`), the global one is off by default, and a 400 on a request that carried a thinking
  budget is retried once without it and remembered for that model.

## Chat evaluation on the real model (2026-10-14/15)

`pit-wall eval --file tests/fixtures/live_lfm_magione_race_window.pwrec.gz --session race` asks 34 questions
about a recorded session: 13 the code answers by itself, 7 open questions, 8 the context cannot answer
(weather, rivals, oil temperature...: the right answer is "no tengo ese dato") and 6 traps with the router off
(the right number on the right wheel or lap). Default model `gemini-flash-lite-latest`, reserve
`gemini-3.1-flash-lite`, paced at one call per 5 s.

| Pass | Passed | What it showed |
|---|---|---|
| 1 | 26 / 34 | a "fase 8" in a local answer (a number the context does not hold); the judge punished a refusal that also quoted a true fact; two transient service errors cascaded into four degraded answers |
| 2 | 31 / 34 | three false positives of **my** checks: a number read in the wrong clause, "la 2" not understood as lap 2, a paraphrase of an alert judged as wrong |
| 3 | 33 / 34 | one **over-refusal**: "no tengo ese dato" to a question about the front/rear axle difference |
| 4 | **34 / 34** | after giving axle and side averages and differences as facts |

Final run: 21 answers from the model, **0 claims removed** by the grounding check, latency median **1.06 s**,
p95 **3.9 s** (the slowest were a 5.2 s and a 3.9 s call; the 3 s target is met by the median, not by every
call). Traps: 6 / 6 in every pass. Impossible questions: every answer the model gave was a clean refusal.

**How to read this honestly**

* Three of the four improvements were to *my evaluator and my heuristics*, not to the model: the model's
  answers were right when my checks said they were wrong. A passing grade here also means "the checks no
  longer produce false alarms on these 34 questions", not "the model is perfect".
* One session (a 6-minute race window of one car on one track), 34 questions, one language: it is a
  regression set and a smoke test, not a benchmark. Temperature is 0.2, so a re-run can differ (the
  over-refusal appeared in one run of three).
* The grounding check is necessary and not sufficient: it catches a number that is absent or on the wrong
  wheel/lap/corner/unit, not a wrong *interpretation* of a correct number.
* The free tier was overloaded now and then (503, read timeouts): the reserve model answered, and a
  degraded answer is retried once in the evaluation.

## Several API keys (reserve keys)

For a long race, more than one key can be given. The first is used until it hits its quota; then the
request goes to the next key **for the same model** (a reserve key has the same quality), and only when every
key is resting does a fallback model get tried.

```
# .env  (git-ignored)
GEMINI_API_KEY=first
GEMINI_API_KEY_2=second
GEMINI_API_KEY_3=third
# or all in one line:
GEMINI_API_KEY_LIST=second,third
```

* `NAME`, `NAME_2` ... `NAME_9` and `NAME_LIST` (comma separated) are read in that order; duplicates are
  dropped. `NAME` is `llm.api_key_env` (default `GEMINI_API_KEY`).
* The key in use is kept while it works (a race spends one key at a time). A key that answers 429 rests for
  the time the API asks (at least an hour when the message is a *daily* quota), then it is tried again.
* A key the API **rejects** (401/403) is dropped for the rest of the run; the others go on. If every key is
  rejected the chat says the model is unavailable.
* `pit-wall llm-check` lists each key as `key 1: accepted`, `key 2: answered 403`... and never prints one.
* Keys never appear in errors, logs or the output of any command.

**A caution on terms.** Quotas are per project. Providers' terms differ on getting extra allowance by
creating several accounts or projects, and some forbid it. This mechanism is meant for keys you are entitled
to use (for example a personal key plus a work or paid key as reserve). Read the provider's terms before
filling the list with free-tier keys from many accounts: that is your decision and your responsibility.
