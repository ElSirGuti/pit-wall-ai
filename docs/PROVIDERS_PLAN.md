# Any provider, one .exe (plan)

Status: **plan only.** Decision of 2026-10-14: the finished project should be an `.exe` where the user picks
the service (Anthropic, OpenAI, Google, Meta models through a host, OpenRouter, a local model, others),
pastes a key and goes. Today only Gemini is implemented, behind the `LLMProvider` interface. Everything said
here about other vendors' endpoints comes from general knowledge, **not** from tests in this project, and is
marked *to verify*: an adapter is not called supported until it has answered a real call (as Gemini did,
with surprises).

*Resumen en español al final.*

## 1. Three wire formats cover almost everything

| Adapter | Covers | Shape (*to verify per vendor*) |
|---|---|---|
| `GeminiProvider` | Google | **done**; `generateContent`, key in a header, `models.list` |
| `OpenAICompatProvider` | OpenAI, OpenRouter, Groq, Together, Mistral, DeepSeek, xAI, and **local** servers (Ollama, LM Studio, llama.cpp); Meta's Llama models through any of those hosts | `POST {base_url}/chat/completions`, `Authorization: Bearer`, `GET {base_url}/models` |
| `AnthropicProvider` | Anthropic | `POST /v1/messages`, `x-api-key` + `anthropic-version`, `GET /v1/models` |

So "any service" is three adapters plus a `base_url`, not a dozen integrations. The compat adapter is the
one that makes OpenRouter, a local model and most "other" services work with the same code; whether Meta
exposes its own API in a compatible form is *to verify*.

Each adapter implements the same two things the chat already depends on: `generate(request)` and
`list_models()`, and raises the same errors (`LLMRateLimited`, `LLMTransient`, `LLMUnavailable`, `LLMError`).
The conversation, the grounding check, the limiter and the cache know nothing about a vendor.

## 2. Configuration: from one provider to a chain

Today: `llm.provider`, `llm.model`, `llm.fallback_models` (reserves of the same provider). Target:

```yaml
llm:
  allow_cloud: true            # consent; local endpoints (localhost) do not need it
  chain:                       # tried in order; the first that answers wins
    - {provider: gemini,        model: gemini-flash-lite-latest, key_env: GEMINI_API_KEY}
    - {provider: openai_compat, base_url: https://openrouter.ai/api/v1, model: <a model>, key_env: OPENROUTER_API_KEY}
    - {provider: openai_compat, base_url: http://localhost:11434/v1,   model: <a local model>}
```

* One **endpoint** = provider kind + base URL + model + key. The old `provider`/`model`/`fallback_models`
  keys keep working and are read as a chain of one provider.
* **Cooldown and quota are per endpoint and model** (what the provider already does per model for Gemini:
  a 429 rests that model for its `Retry-After`; failed requests are not repeated blindly).
* A **local** endpoint has no key and sends nothing out of the machine: the natural last link of the chain.

## 3. "Gemini is always releasing new models": what happens

What already exists, and what is added.

| Situation | Behaviour |
|---|---|
| A model is retired for new users (seen on 2026-10-14: 404 on the old default) | the API's own message and the replacement it names are shown; the chain moves to the next endpoint; **nothing is switched silently** |
| A new, better model appears | it is listed by `list_models()` (nothing is hardcoded); the user picks it in the YAML or the .exe dropdown. A moving alias (`*-latest`) follows releases by itself; its cost is that behaviour, price and data policy can change under it (`answered by:` shows what it resolved to) |
| The user wants stability | pin a concrete name; accept that it may be retired some day and the chain covers that day |
| Different models, different quotas (seen: 500 per day for Flash-Lite, 20 for Flash) | cooldown per model; the daily budget is a hard stop; the reserve is a model with **its own** allowance |
| The model list contains image, speech, music, agent models | filtered to text generation (the listing says which methods each supports; other vendors expose a modality) |

Principle: **the application proposes, the user decides.** A banner "model X is retired, the API suggests Y,
switch?" is fine; an automatic jump to an unknown model is not, because quality, cost and what the provider
does with your data are decisions, not details.

## 4. The `.exe`

A later packaging phase (after the chat and voice work), not a refactor of what exists:

* **Settings window**: provider dropdown, key field, model dropdown filled from `list_models()`, a **Test**
  button that runs what `llm-check` runs, the consent checkbox with the provider's own data notice, and the
  quota hint ("check your usage page").
* **Key storage**: Windows Credential Manager (through `keyring`), not a plain `.env`; the `.env` route stays
  for the command line.
* **Packaging**: PyInstaller. Known costs to plan for: antivirus false positives on unsigned executables
  (code signing), the size with SAPI/pywin32, an update path, and a licence check of every bundled
  dependency. The game's own files are never bundled (the geometry is generated from the user's install).
* **First run**: detects the game and the tracks folder, offers the provider choice, runs the test.

## 5. Privacy per provider

The consent text is **per endpoint**: a free tier may use inputs to improve the vendor's products, a paid API
usually does not, a local model sends nothing out. The `allow_cloud: false` default stays. Rival names (phase
8) are anonymised before any prompt, whichever provider.

## 6. Order of work (proposal)

1. Finish the chat (steps 6 and 7 of phase 5) against Gemini, the only adapter with a real-service history.
2. `OpenAICompatProvider` with a fake-transport test suite like Gemini's; first real checks against OpenRouter
   and a local server (the cheapest ways to test it).
3. `AnthropicProvider`.
4. The `chain` configuration and per-endpoint cooldown, then the model-retired banner.
5. The packaging phase.

Each adapter ships as "implemented, tested with simulated answers" until a real key has answered a call and
the differences have been written down in `docs/LLM.md`.

## 7. Questions for the author

1. Which providers do you want to try first besides Gemini (the compat adapter makes OpenRouter and a local
   model nearly free once it exists)?
2. Is a local model (Ollama/LM Studio) interesting to you, or only cloud services?
3. Should the key live in the Windows Credential Manager from the start of the packaging phase?

---

## Resumen en español

"Cualquier servicio" son **tres adaptadores**, no una docena: Gemini (hecho), uno **compatible con OpenAI**
(cubre OpenAI, OpenRouter, Groq, Together, Mistral, DeepSeek y modelos locales como Ollama o LM Studio; los
modelos de Meta llegan por esos anfitriones) y uno para Anthropic. La configuración pasa de "un proveedor y
sus reservas" a una **cadena de endpoints**, cada uno con su propio enfriamiento y cupo; un modelo local es
el último eslabón natural. Con los modelos nuevos de Gemini: se listan en vivo (no hay nombres fijos en el
código), un modelo retirado muestra el mensaje y el reemplazo que sugiere la API y la cadena pasa al
siguiente; **la aplicación propone y tú decides**, nunca salta sola a un modelo que no elegiste. Un alias
`*-latest` sigue las novedades pero puede cambiar comportamiento, precio y política de datos. El `.exe`
(fase de empaquetado) tendrá ventana de ajustes, clave en el Administrador de credenciales de Windows y botón
de prueba. Nada de otros proveedores se da por soportado hasta que responda una llamada real.
