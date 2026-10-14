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
