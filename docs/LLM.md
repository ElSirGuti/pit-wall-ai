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

## Measured on the author connection

| Item | Value |
|---|---|
| models available to the key | _pending_ |
| latency, median / max of 3 tiny calls | _pending_ |
| quota seen in AI Studio | _pending_ |
| 429 behaviour (retry hint) | _pending_ |
