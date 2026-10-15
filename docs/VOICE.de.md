# Sprache: mit dem Ingenieur sprechen

*English: [VOICE.md](VOICE.md)*

Halte eine Taste, frag, lass los und hör die Antwort. Die Regelwarnungen (Blockieren, Sprit, Rundenzusammen-
fassungen ...) kommen weiter von selbst. Diese Seite ist die Benutzeranleitung; Design und Messungen stehen in
`docs/PHASE6_PLAN.md`.

## 1. Installation

```
pip install -e ".[voice,stt]"          # Mikrofon + Windows-Stimme + lokale Spracherkennung
pip install -e ".[stt,stt-cuda]"       # nur, wenn die Erkennung auf der NVIDIA-GPU laufen soll
```

Das Modell der Spracherkennung (rund 0,5 GB für `small`) wird beim ersten Gebrauch heruntergeladen. Wenn der
Download festzuhängen scheint, setze `HF_HUB_DISABLE_XET=1` und starte erneut.

## 2. Geräte auswählen (einmalig)

```
pit-wall devices                       # Mikrofone und Audio-Ausgänge, die konfigurierten sind markiert
pit-wall mic-test --device seiren      # nimmt 3 s auf: Pegel, Übersteuerung, und eine WAV auf Wunsch (--out)
pit-wall voices --test                 # spielt eine Probe mit jeder installierten Stimme auf dem gewählten Ausgang
pit-wall bind push_to_talk             # die Taste deines Lenkrads / deiner Tastatur / Maus drücken
```

Die Auswahl kommt in `config/local.yaml`:

```yaml
audio:
  input_device: seiren                 # ein Teil des Mikrofonnamens
engineer:
  language: de                         # es | en | de
  voice:
    name: Katja                        # ein Teil des Stimmennamens
    output_device: headset             # ein Teil des Ausgangsnamens
```

Weitere Stimmen: Windows-Einstellungen > Zeit und Sprache > Sprache > Stimmen hinzufügen (sie erscheinen in
`pit-wall voices`). Für Deutsch bringt „Deutsch (Deutschland)“ Hedda und Katja mit. Ein anderer Synthesizer:
`engineer.voice.backend: command` mit eigenem Befehl (siehe `config/default.yaml`).

## 3. Benutzung

```
pit-wall voice                         # live: das Spiel, die Lenkradtaste, das Mikrofon, die Stimme
pit-wall voice --file race.pwrec.gz --wav frage.wav     # simuliert: ohne Spiel, Lenkrad und Mikrofon
```

Halte die Taste und frag in eigenen Worten („Wie viel Sprit habe ich noch?“, „Wie viel habe ich in Kurve 6
verloren?“, „Was hast du mir über Kurve 4 gesagt?“). Aufnahmen vom Telefon (m4a, mp3, ogg ...) funktionieren im
simulierten Modus.

## 4. Die Funkregeln

* **Wer zuerst anfängt, gewinnt** (`radio.priority: first_to_start`). Spricht der Ingenieur gerade, wird dein
  Tastendruck ignoriert; mit `radio.priority: driver` unterbricht deine Taste stattdessen den Ingenieur.
* **Kritische Warnungen gehen immer raus**, auch während du sprichst.
* Braucht die Antwort mehr als `radio.ack_after_s` (1,5 s), sagt der Ingenieur zuerst „Einen Moment.“.
* Die Taste `acknowledge` unterbricht eine gerade gesprochene Meldung.

## 5. „Nicht stören“

Sag „Nicht stören“, „Jetzt nicht“, „Ruhe“ (oder drücke die Taste `mute_engineer`): Der Ingenieur sagt nur noch
die kritischen Warnungen, bis du wieder sprichst (`radio.quiet_ends_on_speech: true`) oder „Du kannst wieder
sprechen“ sagst. Spanisch: „No molestes ahorita“, „Ya puedes hablar“. Englisch: „Do not bother me now“, „You
can talk again“.

## 6. Sprachen und Stil

`engineer.language` ist `es`, `en` oder `de` und bestimmt den Text, die Sprache des Modells, die Sprache der
Erkennung und die Stimme. `engineer.persona` gibt dem Ingenieur einen Namen, spricht dich mit deinem an und
legt eine Art zu sprechen fest (`neutral | brief | calm | technical | warm | direct` und eigene Hinweise); es
ändert nur die Formulierung.

## 7. Wo es läuft und wie schnell

Auf dem PC des Autors (i5-11400, GTX 1660 SUPER) mit dem Modell `small`: 1,9 s pro Frage auf der CPU und
0,4 s auf der GPU bei einer Äußerung von 2 bis 3 s; eine lokale Antwort ist in etwa 0,01 s fertig, der ganze
Durchgang dauert also etwa 0,4 s nach dem Loslassen der Taste (hinzu kommt der Start der Stimme, und eine
Cloud-Antwort etwa 1 s). Wo die Erkennung laufen soll, ist eine Entscheidung pro Spiel (`stt.device: auto |
cpu | cuda`): Miss es mit `pit-wall stt-test --device both --repeat 30` bei geöffnetem Spiel und vergleiche
die Bildrate.

## 8. Wenn etwas nicht funktioniert

| Symptom | Wo nachsehen |
|---|---|
| „no microphone matches ...“ | `pit-wall devices`: einen Teil des genauen Namens verwenden |
| Pegel 0 % im `mic-test` | die Windows-Eingangslautstärke; ein Bluetooth-Headset im Freisprechmodus arbeitet mit 8 kHz und ist für Spracherkennung schlecht |
| „recognition unavailable: ... cublas64_12.dll“ | `pip install -e ".[stt,stt-cuda]"`, oder `stt.device: cpu` |
| die Taste tut nichts | `pit-wall input-test` zeigt, ob der Druck ankommt (G HUB muss für das G29 laufen) |
| die Antwort sagt „no puedo consultar el modelo“ | `.env` hat keinen Schlüssel oder `llm.allow_cloud` ist false: lokale Antworten funktionieren trotzdem |
| ein Wort wird immer falsch verstanden | es kann in `stt/postfix.py` (eine kleine Tabelle gemessener Verhörer) |
