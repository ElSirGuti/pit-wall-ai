# Voices plan: neural voices, voice profiles and the radio effect

Status: **proposal, waiting for the author's approval. No code yet.** Origin: a conversation the author
shared on 2026-10-15 about voice cloning for the engineer.

## 0. What is wanted

A voice for the engineer that sounds like a person on a team radio, chosen by the user, running locally, with
the latency of a race engineer. Today there are Windows (SAPI) voices and a `command` backend that calls any
synthesiser; this plan makes the good local options first-class and adds the radio sound.

## 1. The engines

| Engine | Quality | Latency (the author's PC: i5-11400, GTX 1660 SUPER 6 GB) | Cost on the game | Notes |
|---|---|---|---|---|
| Windows SAPI (today) | plain | tens of ms | none | works everywhere, few voices |
| **Piper** (local neural) | good, natural | well under a second on the CPU | CPU only | open voices in Spanish, English and German; the sensible default for a heavy sim |
| **XTTS / F5-TTS / GPT-SoVITS** (zero-shot, local) | the best, clones a timbre from a short clip | 1 to 4 s per sentence on this GPU | **uses the GPU the game needs** | for the pre-rendered phrases, not for in-corner alerts |
| A cloud TTS | good | network | none locally | audio leaves the PC: needs the same consent as `llm.allow_cloud` |

The project already has the seam: `TtsBackend` and `create_backend`. New backends are small adapters
(`piper`, `http` for a local server such as GPT-SoVITS' API) that return a WAV.

## 2. Latency strategy

* **Pre-render the frequent phrases** once per voice (the fixed alert texts, "Un momento", the milestone
  variants) into a local cache keyed by voice and text; speaking them is then a file play.
* **Dynamic sentences** (numbers, names) go through the fast engine (Piper) or the SAPI voice; the slow
  zero-shot engine is for the cache and for the debrief, never inside a corner.
* The engine of a profile can differ by message class: alerts fast, debrief rich.

## 3. Voice profiles

`voice_profiles/<id>.yaml`, **local and git-ignored by default**, one per voice:

```yaml
id: mi_ingeniero
display_name: "Marta"          # what the engineer calls themself
engine: piper                  # sapi | piper | http | command
model: "es_ES-davefx-medium"   # engine specific
reference_audio: null          # only for zero-shot engines: a clip the user has the right to use
language: es
radio_effect: true
```

The persona (`engineer.persona`: name, style, notes) stays separate from the voice: a style changes the
wording, a voice changes the sound; any combination is allowed.

## 4. The radio effect

A light DSP on the synthesised audio, optional (`radio_effect`), written with numpy only:

* band-pass roughly 300 Hz to 3.4 kHz (the narrow channel of a team radio);
* a soft compressor and a very light bit-crush;
* a short squelch click at the start and the end, and low noise that fades in and out with the transmission.

It also hides small synthesis artefacts. Rendering to a WAV first means playback goes through the chosen
output device (`sounddevice`), like the microphone does. It adds a few milliseconds; the per-message latency
stays measured by `pit-wall say`.

## 5. What this project will not do

* **It does not ship, name or preset the voice of any real person**, and it does not include tools to pull
  audio of third parties. The personas of this project are *styles*, not impersonations (an earlier
  decision of the author; see `docs/ENGINEER_EXPERIENCE_PLAN.md`).
* A zero-shot profile uses a reference clip **the user supplies and has the right to use**: their own voice,
  a voice they were given permission for, or a clearly licensed one. The README says so, next to the setting.
* Nothing is uploaded: reference audio and models stay on the PC.

This is a legal and ethical line, not a technical limit: the mechanism works with any clip, and using a real
person's voice without their consent can be a problem for the user even in personal use, and certainly if the
result is published or distributed.

## 6. Steps (each waits for approval)

| Step | Content | Done when |
|---|---|---|
| 1 | `radio_effect` on any backend (render to WAV, DSP, play on the chosen output) + a test that checks the spectrum | audible on the author's PC; latency reported |
| 2 | `piper` backend + model download helper + `pit-wall voices` lists it | a Spanish and a German voice work offline, CPU only |
| 3 | phrase cache (pre-render) | the fixed alerts start as fast as SAPI |
| 4 | `http` backend for a local zero-shot server + profile files | one real zero-shot voice from the author's own clip |
| 5 | docs, README ES/EN, examples without any real person | |

## 7. Risks

* **VRAM**: the game and a zero-shot model fight for 6 GB; hence the CPU default and the cache.
* **Dependencies**: Piper and the zero-shot servers are installed by the user; the project only talks to
  them (a `voice` extra stays small).
* **Windows audio**: device selection through `sounddevice` has to be verified on the author's outputs.
* **Licences of models and voices** differ: the plan lists them next to each model in the docs.

## 8. Questions for the author

1. Is CPU Piper as the default for heavy sims acceptable, with the GPU engines optional?
2. Do you want a radio effect strength setting (light / strong) or on / off only?
3. Should the fixed phrases be pre-rendered at install time or the first time they are spoken?

---

## Resumen en español

El ingeniero necesita una voz local que suene a radio de equipo y con latencia de ingeniero. Hoy hay voces de
Windows y un backend `command`; el plan añade **Piper** (neuronal local, solo CPU, bueno para sims pesados),
un backend `http` para servidores zero-shot locales (XTTS, F5-TTS, GPT-SoVITS; usan la GPU del juego, por eso
se reservan a frases pre-renderizadas y al debrief), una **caché de frases**, **perfiles de voz** locales y un
**efecto de radio** (paso de banda 300 Hz a 3,4 kHz, compresión, clic de squelch y ruido suave). Línea que no
se cruza: el proyecto **no incluye ni nombra voces de personas reales** ni herramientas para extraerlas; los
perfiles zero-shot usan un clip que **el usuario aporta y tiene derecho a usar**, y las personas del proyecto
siguen siendo estilos, no imitaciones. Cinco pasos, cada uno con tu aprobación.
