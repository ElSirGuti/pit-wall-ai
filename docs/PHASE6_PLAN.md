# Phase 6 plan: full voice

Status: **approved 2026-10-15; step 1 (buttons) implemented**, the rest is not. The author's answers are in
section 7. What is said about libraries, models and the Gemini Live API comes from general knowledge and from
the quota page the author shared, and is marked *to verify*; the **latency and accuracy numbers are to be
measured on the author's PC**, not assumed.

*Resumen en español al final.*

**Update 2026-10-18:** the voice is used live in practice, qualifying, a race and an online race. The 40 phrases were measured (`docs/FRASES_VOZ.md`): `small` 38/40 and 1.4 % word errors in silence, with the engine on 37/40 and 3.2 %; `medium` 39/40 and 0.9 % with the engine on, at about 2.5 s from release to answer, and it is the model set in the author's configuration for driving. Greetings, thanks and radio checks are answered locally (`chat/smalltalk.py`), and the "one moment" counts the wait for the answer, not the recognition.

## 0. Goal and non-goals

**Goal.** Hold a button, speak a question, release, and hear the answer (the same chat of phase 5), without
touching the keyboard; while the alerts of phase 3 keep their priority and never get lost behind a conversation.

**Non-goals.** A wake word or always-on listening (privacy and false triggers; push-to-talk only); setup and
strategy advice (phase 8); streaming conversation with interruptions at human speed; replacing the rule-based
alerts with the model (they stay local and instant).

## 1. The pipeline

```
button (any device)  ->  microphone capture  ->  speech-to-text  ->  ChatSession (phase 5)  ->  speech output
   [input/]               [audio/]               [stt/]               local answer | model       [voice/]
                                                                         |
                          rule alerts (phase 3) -----------------------> priority queue ---------+
```

Every stage runs outside the telemetry loop (its own thread), and none of them may delay it.

### 1.1 Buttons (`input/`)

The contract is `docs/INPUT_BINDINGS.md`: any action on any button of any device, bound by pressing it.
Phase 6 needs two actions: `push_to_talk` (hold) and `cancel` (tap). The rest (feedback buttons, mute...) come
with phase 8.

* A `FakeBackend` for tests, so hold / tap / chord / conflict logic is covered without hardware.
* Windows backends, *to verify on the author's devices*: joystick buttons through WinMM (`joyGetPosEx`, no
  dependency, up to 32 buttons: enough for a G29) and keyboard and mouse through low-level hooks; SDL2/pygame
  stays as the fallback for devices with more buttons. G HUB is installed: whether it swallows or remaps the
  button must be tested.

### 1.2 Microphone capture (`audio/`)

* 16 kHz, mono, 16-bit, the format speech models want. `sounddevice` (PortAudio) as an optional dependency
  (`pip install -e ".[voice]"`), device chosen by name, with `pit-wall mic-test` to see levels and record 3 s.
* On press: a short **pre-roll** (the 200 ms before) so the first syllable is not lost; on release: a 150 ms
  tail. Utterances shorter than 0.3 s or with no energy are discarded as accidental presses.
* The audio is **not stored** by default; with `voice.keep_audio: true` the last N utterances are kept in a
  local folder for debugging and are never committed.

### 1.3 Speech to text (`stt/`)

An interface `Transcriber.transcribe(pcm, language, hints) -> Transcript(text, latency)`, with backends:

| Backend | For | Against |
|---|---|---|
| **faster-whisper, local** (default candidate) | no network, no quota, audio stays on the PC | CPU or GPU time while the game runs; accuracy depends on the model size |
| Gemini transcription (cloud) | accurate, no local load | audio leaves the PC (more sensitive than telemetry), quota (the quota page showed 3 requests per minute and 25 per day for the batch transcribe model; the Live variant is listed without a request limit and 20 K tokens per minute) |

* **Vocabulary biasing**: the corner names of the circuit, the car and the words of the chat ("Curvone", "Prima
  Esse", "delantero izquierdo", "psi") are passed to the model as a prompt/hotwords: a name that is not in its
  vocabulary is what a generic recogniser gets wrong.
* **Where the compute goes** is the main open question, see the risks: this PC has a 6-core i5-11400 and a
  GTX 1660 SUPER with 4 GB, and the same GPU draws the game.

### 1.4 The conversation

The transcript goes to `ChatSession.ask` unchanged (local answers, model, grounding, degradation). The
confidence of the recogniser, when it is low, is shown ("Entendí: «...»") instead of answered blindly, and a
transcript that is empty or noise is dropped with a short "No te he entendido".

### 1.5 Speech output (`voice/`, extends phase 3)

`SpeechOutput` exists (SAPI, Sabina, one thread, queue, interrupt). Phase 6 gives it **priorities and
barge-in**:

| Event | Behaviour |
|---|---|
| the driver presses push-to-talk | whatever is being said is cut at once (the driver asked for the floor); queued non-critical alerts wait |
| a **critical** alert (spin, puncture, fuel) while the driver is talking or an answer is playing | goes out anyway and cuts the answer: safety first |
| a medium/low alert while an answer is playing | waits for the answer to finish, then speaks if it has not expired (TTL) |
| an answer | plays in full unless cut by a critical alert or by a new press |
| two answers in a row | the older one is dropped (the driver already asked again) |

The calm-moment rule of phase 3 still applies to alerts; an **answer** is a reply to the driver and is not held
for a straight.

## 2. Latency budget (targets, to be measured)

| Stage | Target |
|---|---|
| release of the button -> transcript | 0.4 to 1.5 s (depends on model and compute) |
| transcript -> answer text | local under 50 ms; model about 1 s (median 1.06 s measured in phase 5) |
| text -> first audio (SAPI) | 0.1 to 0.25 s (89 to 110 ms warm, 242 ms cold, measured in phase 3) |
| **release -> first audio** | **median under 4 s**; local answers under 2 s |

The phase 5 chat target (answer under 3 s) is for the text; voice adds the recognition and is reported
separately.

## 3. The Gemini Live API: evaluated, not adopted by default

The spec asks to evaluate it. It does the whole loop (speech in, speech out, streaming) in one session. As a
**separate spike** after step 4, compared against the pipeline above on the same phrases:

| Question | How it is measured |
|---|---|
| time from end of speech to first audio | same stopwatch as the pipeline |
| does it keep to the numbers? | the grounding check applies to its text transcript only if the API returns one *(to verify)*; without it the numbers cannot be checked: a **blocking** point, given principle 1 of the project |
| can the facts be fetched by function calling instead of pasted in the prompt? | *(to verify)* |
| session limits | audio-only sessions are limited in time *(to verify; ~15 min was noted earlier)* and reconnecting costs seconds |
| voice and language | Spanish quality, and that it is not "Sabina" |
| privacy and quota | the microphone audio goes to the provider; the free tier may use it |

Decision rule: it is adopted as an **option** only if it matches the grounding guarantee; otherwise it stays
documented and unused.

## 4. Steps

| Step | Content | Done when |
|---|---|---|
| 1 | bindings + `pit-wall input-test`, `FakeBackend`, WinMM/hook spike on the G29, keyboard and mouse | **implemented**; the G29 is seen and the keyboard hook verified with an injected key; **pending: a real press of the wheel with G HUB running** (`docs/PENDIENTES.md`) |
| 2 | audio capture + `pit-wall mic-test` | levels, 3 s recording, device selection, pre-roll and tail verified |
| 3 (**implemented; measured on the PC with synthetic phrases, see 7.1**) | `Transcriber` interface + faster-whisper backend + `pit-wall stt-test file.wav` | latency and word error rate on the author's own phrases, CPU vs GPU, **and the effect on the game's frame rate** |
| 4 (**implemented, simulated end to end; live with the wheel pending**, see 7.2) | `pit-wall voice`: button -> STT -> chat -> speech with priorities and barge-in | works end to end on a replayed session; critical alert cuts an answer; a press cuts speech |
| 5 | evaluation: 30 to 40 spoken questions, end-to-end latency report; Gemini Live spike | numbers reported, decision on Live recorded |
| 6 (**done**) | README ES/EN, `docs/VOICE.md`, pending list updated | the guide exists and the commands are in the README |

Each step is delivered with tests (hardware replaced by fakes where there is no hardware) and **waits for the
author's approval** before the next.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Local STT steals CPU/GPU from the game and drops its frame rate | measure it (step 3); run on the CPU at low priority with few threads and only while an utterance is processed; a smaller model; a cloud option behind the consent flag |
| Accuracy in a noisy car/desk (engine, wheel, a desktop microphone that also hears the speakers) | push-to-talk only; vocabulary biasing; show what was understood; the recommendation to use a headset; evaluate with background sound |
| Numbers misheard ("15" / "50") change the question | echo the understood question when it contains numbers or names; the confirmation is cheap |
| The button is not seen while the game has the focus, or G HUB eats it | spike in step 1 on the real devices; a keyboard fallback binding |
| Echo: the microphone hears the answer | a press cuts the speech; a short guard time after speech before capture |
| Speech overlaps with critical alerts | the priority table of section 1.5 |
| Audio is more sensitive than telemetry | local STT by default; the cloud one only with explicit consent and the notice per provider; audio not stored |
| A heavy dependency (CUDA libraries, models of hundreds of MB) in the future `.exe` | optional extra; model downloaded on first use; the plain text chat works without it |
| Windows microphone privacy settings and exclusive-mode devices | `mic-test` says what it found and what to change |

## 6. Questions for the author

1. **Which button** is push-to-talk (a G29 paddle or wheel button, a keyboard key, a mouse button)? Any is
   valid; the point is one that the game does not use.
2. **Which microphone** do you speak into: the Razer Seiren Mini (desktop) or a headset? With the desktop
   microphone the speakers' sound reaches it; a headset is strongly better.
3. **Where may the recognition run**: local on the CPU (my default proposal), local on the GPU (faster, but it
   is the game's GPU), or cloud with consent?
4. **Where should the voice come out**: the speakers or a headset?
5. Will you **record the sample phrases** (30 to 40) once step 3 is approved?
6. Is it acceptable that an **answer cuts** an alert of medium priority when you press the button (the alert is
   lost if it expires)?

## 7. The author's answers (2026-10-15)

1. **Button:** the author will configure it with `pit-wall bind push_to_talk` (any device).
2. **Microphone:** selectable by name; usually a headset, sometimes the Razer Seiren Mini. Needs a device list
   and a `voice.input_device` setting (step 2).
3. **Where recognition runs:** both CPU and GPU must be tried and left as a user setting, because the load
   differs by game (iRacing is CPU-hungry). Settings: `stt.device: auto | cpu | cuda`, model size, threads,
   process priority. Step 3 compares them on this PC, including the effect on the frame rate.
4. **Voice output:** selectable by name too (`voice.output_device`).
5. **Alert vs answer policy**: the author pointed to how the F1 radio works (half-duplex: one party talks at
   a time, a collision garbles the message, the driver acknowledges with a word or the ACK button). The
   policy of section 1.5 is replaced by the **channel model** of `docs/ENGINEER_EXPERIENCE_PLAN.md`
   (section 1): the driver wins on PTT, the cut message is re-queued if still relevant, critical alerts
   always go out, an immediate acknowledgement phrase hides the latency, and an `acknowledge` action
   exists.

6. **Radio contention (decided 2026-10-16): whoever starts first wins.** If the engineer is already
   transmitting, the driver's PTT does not cut it (a short "busy" tone, the question is kept and sent when the
   channel frees); if the driver is already talking, an incoming alert waits. **Critical alerts always go
   out** (they may interrupt a non-critical message). Setting: `radio.priority: first_to_start | driver`,
   default `first_to_start`. Not implemented yet (step 4).
7. **Volunteered events (decided):** by default only **critical and important** ones are spoken on their own;
   the rest are available on request. Setting: `engineer.talkativeness` (to design in 6b).
8. **Voices (requested 2026-10-16):** any voice installed in Windows is selectable by name
   (`engineer.voice.name`), including ones the user downloaded (Settings > Time & language > Speech > Add
   voices); other engines through `engineer.voice.backend: command`. `pit-wall voices` lists them and
   `pit-wall voices --test [--voice X] [--device Y]` plays a sample with each so they can be judged by ear;
   `pit-wall say --voice X --device Y "text"` tests one. Step 2 status: listing verified on this PC;
   audible playback and microphone recording need the author at the PC.

Also from the author: brake temperature is not simulated by every car of Assetto Corsa (a Kunos forum
statement of 2017 lists the Cobra, the F40 and some classic Porsches). It matches what was measured here
(a flat sensor on two cars) and is handled without a list of cars: a sensor that does not move is ignored.

### 7.1 Step 3 measurements (2026-10-14, this PC: i5-11400, GTX 1660 SUPER 4 GB, model `small`, beam 1)

Eight spoken questions **synthesised with the Windows voice Sabina** (not the author's voice, no microphone, no
engine noise), so they measure speed and gross errors only:

| Where | Load | Latency per phrase (2 to 3 s of audio) | Real-time factor | Word error rate |
|---|---|---|---|---|
| CPU, 4 threads, int8 | 122 s the first time (download), a few seconds afterwards | 1.87 s mean (1.75 to 2.10) | 0.76 | 8.9 % over 45 words |
| GPU (CUDA, float16) | 3.3 s | 0.41 s mean (0.36 to 0.46) | 0.17 | 8.9 % (same errors) |

* The errors are the same on both: "psi" heard as "sencaí" and digits ("curva 6" vs "seis"). The digit case is
  a spelling difference that the chat router must accept; "psi" needs the recogniser vocabulary (hotwords did
  not fix it with this model) or a post-correction table: **open**.
* CUDA needs cuBLAS and cuDNN 12, which the NVIDIA wheels provide (`pip install -e ".[stt,stt-cuda]"`); without
  them the model loads and then fails at the first call, so `load()` now warms the model up and reports it.
* **Not measured yet**: the effect on the game's frame rate (needs the author, the game and the wheel) and the
  accuracy with the author's voice and microphone. The 0.4 s GPU latency is the reason to prefer the GPU in
  AC; in iRacing the CPU may be the better choice: that is what `stt.device` is for.

**Follow-up (2026-10-14).** "psi" heard as "sencaí" is now corrected by a short table of measured
mis-hearings (`stt/postfix.py`, applied before the question reaches the chat; it grows only from real
recordings). Spoken corner numbers ("curva seis") are understood by the corner lookup.

### 7.3 "Do not bother me" (requested 2026-10-14)

A spoken order ("no molestes ahorita", "silencio", "ahora no") or the `mute_engineer` button puts the
engineer in **quiet mode**: the pending alerts are dropped and everything below CRITICAL stays unsaid, while
critical alerts still go out. It ends when the driver speaks again (`radio.quiet_ends_on_speech: true`) or
with "ya puedes hablar"; with the setting off only the order ends it. Spoken orders are matched only on short
utterances (at most 8 words) so a long question that contains "silencio" is not taken as an order
(`chat/commands.py`). Verified with fakes; not yet heard on the real voice.

### 7.2 Step 4: the voice turn (2026-10-14)

`pit-wall voice` runs button -> utterance -> recognition -> chat -> speech. Radio rules (decided with the
author): whoever starts first wins (`radio.priority`), critical alerts always go out, an acknowledgement
("Un momento.") is spoken when the answer takes longer than `radio.ack_after_s`, `acknowledge` cuts a
non-critical message. The rule alerts wait while the driver talks or an answer is prepared (the engineer
already keeps pending alerts and drops the stale ones).

Simulated end to end on this PC (`pit-wall voice --file lfm_carrera.pwrec.gz --wav ... --stt-device cuda`,
four questions synthesised with Sabina): from the end of the utterance to the answer ready, **0.39 to
0.51 s** (recognition about 0.4 s, local chat 0.01 s) on the GPU. Not included: the time the voice takes to
start (about the `say` latency measured in phase 3) and the cloud model when a question needs it (seconds).
Not verified: the live path (wheel button, microphone, game telemetry together), which needs the author.

---

## Resumen en español

**Qué**: mantienes un botón, hablas, sueltas y oyes la respuesta del chat de la Fase 5, sin tocar el teclado.
Los avisos por reglas siguen siendo locales e instantáneos y mantienen su prioridad.

**Cómo**: botón (cualquier dispositivo, `docs/INPUT_BINDINGS.md`) -> captura del micrófono (16 kHz, con un
pequeño pre-roll) -> reconocimiento de voz local con faster-whisper, sesgado con los nombres de curvas del
circuito -> `ChatSession` -> voz Sabina con **prioridades**: pulsar el botón corta lo que se dice, una alerta
crítica corta una respuesta y las demás esperan.

**Latencia objetivo** (a medir): de soltar el botón al primer audio, mediana por debajo de 4 s; las respuestas
locales por debajo de 2 s.

**Gemini Live**: se evalúa como un experimento aparte; solo se adoptará como opción si respeta la comprobación
de anclaje de los números, que hoy es lo que no sabemos.

**Riesgo principal**: que el reconocimiento local le robe CPU o GPU al juego. Esta PC es un i5-11400 con una
GTX 1660 SUPER de 4 GB que también dibuja el juego; se mide el efecto en los fotogramas antes de decidir.

**Necesito de ti**: el botón, el micrófono (mejor una diadema que el Seiren de escritorio), dónde puede correr
el reconocimiento, y las 30 a 40 frases grabadas cuando llegue el paso 3.
