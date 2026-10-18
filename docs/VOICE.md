# Voice: talk to the engineer

*Deutsch: [VOICE.de.md](VOICE.de.md)*

Hold a button, ask, release, and hear the answer. The rule alerts (lock-ups, fuel, lap summaries...) keep
going out by themselves. This page is the user guide; the design and the measurements are in
`docs/PHASE6_PLAN.md`.

## 1. Install

```
pip install -e ".[voice,stt]"          # microphone + Windows voice + local speech recognition
pip install -e ".[stt,stt-cuda]"       # only if you want the recogniser on the NVIDIA GPU
```

The speech-recognition model (about 0.5 GB for `small`) is downloaded the first time it is used. If the
download seems stuck, set `HF_HUB_DISABLE_XET=1` and run it again.

## 2. Choose the devices (once)

```
pit-wall devices                       # microphones and audio outputs, with the ones you configured marked
pit-wall mic-test --device seiren      # records 3 s: level, clipping, and a WAV if you ask (--out)
pit-wall voices --test                 # plays a sample with every installed voice, on the chosen output
pit-wall bind push_to_talk             # press the button of your wheel / keyboard / mouse
```

Put the choices in `config/local.yaml`:

```yaml
audio:
  input_device: seiren                 # a part of the microphone's name
engineer:
  language: es                         # es | en | de
  voice:
    name: Sabina                       # a part of the voice's name
    output_device: headset             # a part of the output's name
```

More voices: Windows Settings > Time & language > Speech > Add voices (they show up in `pit-wall voices`).
Another synthesiser: `engineer.voice.backend: command` with your own command (see `config/default.yaml`).

## 3. Use it

```
pit-wall voice                         # live: the game, the wheel button, the microphone, the voice
pit-wall voice --file race.pwrec.gz --wav question.wav     # simulated: no game, no wheel, no microphone
```

Hold the button and ask in your own words ("¿cuánta gasolina me queda?", "¿cuánto perdí en la curva 6?",
"¿qué me dijiste de la curva 4?"). Phone recordings (m4a, mp3, ogg...) work for the simulated mode.

## 4. The radio rules

* **Whoever starts first wins** (`radio.priority: first_to_start`). If the engineer is speaking, your press
  is ignored; with `radio.priority: driver` your button cuts the engineer instead.
* **Critical alerts always go out**, even while you talk.
* When the answer takes more than `radio.ack_after_s` (1.5 s) the engineer says "Un momento." first.
* The `acknowledge` button cuts a message that is being said.

## 5. "Do not bother me"

Say "no molestes ahorita", "silencio", "ahora no" (or press the `mute_engineer` button): the engineer says only
the critical alerts until you speak again (`radio.quiet_ends_on_speech: true`) or say "ya puedes hablar".
German: "Nicht stören", "Du kannst wieder sprechen". English: "Do not bother me now", "You can talk again".

## 6. Languages and style

`engineer.language` is `es`, `en` or `de` and selects the text, the model's language, the recogniser's
language and the voice. `engineer.persona` gives the engineer a name, calls you by yours, and sets a manner
(`neutral | brief | calm | technical | warm | direct` and your own notes); it changes the wording only.

## 7. Where it runs and how fast

On the author's PC (i5-11400, GTX 1660 SUPER) with the `small` model: 1.9 s per question on the CPU and
0.4 s on the GPU for a 2 to 3 s utterance; a local answer is ready in about 0.01 s, so the whole turn is
about 0.4 s after the button is released (the first audio adds the voice's start-up, and a cloud answer
about 1 s). Where it should run is a per-game choice (`stt.device: auto | cpu | cuda`): measure it with
`pit-wall stt-test --device both --repeat 30` while the game is open and compare the frame rate.

## 8. The radio sound, other voices and the phrase tests

* **Team-radio sound** over any voice: `engineer.voice.radio_effect: light | strong | off`. Hear it first with
  `pit-wall say "text" --radio strong` (or `--out radio.wav` to write the file). The sentence is rendered to a WAV, given
  the narrow band, a soft compressor, a light bit-crush, low noise and a squelch click at both ends, and played through the
  chosen output; it adds a few tens of milliseconds.
* **Piper** (local neural voice, CPU only): `pip install piper-tts`, `pit-wall voices --piper-download es_ES-davefx-medium`
  and put the lines it prints in `config/local.yaml` (`backend: piper`, `piper_model: ...`).
* **Cache**: the fixed phrases (alert texts, "un momento", the milestone variants) are rendered once per voice; do it ahead of
  time with `pit-wall voices --prerender`. `engineer.voice.cache: false` turns it off.
* **Profiles**: `voice_profiles/<id>.yaml` (local, git-ignored) sets the engine, the voice, the radio effect and the
  engineer's name; `engineer.voice.profile: <id>` selects it. A **local zero-shot server** (XTTS, GPT-SoVITS...) is used
  through the `http` engine (loopback addresses only) with a reference clip that **you supply and have the right to use**;
  nothing is uploaded and this project ships no voice of a real person.
* **Your own phrases**: `pit-wall phrases record --set A` shows the 40 phrases of `docs/FRASES_VOZ.md` one by one and records
  them (Enter to start, Enter to stop; `--set B` with the engine on, `--lang de|en` for the other languages);
  `pit-wall phrases test --set A --device cuda --model medium` reports the word error rate and the latency per phrase.

## 9. When something does not work

| Symptom | Look at |
|---|---|
| "no microphone matches ..." | `pit-wall devices`: use a part of the exact name |
| level 0 % in `mic-test` | the Windows input volume; a Bluetooth headset in hands-free mode works at 8 kHz and is poor for speech |
| "recognition unavailable: ... cublas64_12.dll" | `pip install -e ".[stt,stt-cuda]"`, or `stt.device: cpu` |
| the button does nothing | `pit-wall input-test` shows whether the press arrives (G HUB must be running for the G29) |
| the answer says "no puedo consultar el modelo" | `.env` has no key or `llm.allow_cloud` is false: local answers still work |
| a word is always misheard | it can go into `stt/postfix.py` (a small table of measured mishearings) |
