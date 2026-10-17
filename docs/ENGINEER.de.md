# Der Renningenieur (Phase 3)

Regelbasierte Warnungen, gesprochen von einer lokalen Stimme. Kein Netzwerk, kein Sprachmodell: der Weg von
einem Telemetrie-Sample bis zum Sprachaufruf ist reines Python und unten gemessen.

## Wie eine Meldung entschieden wird

1. `engineer/rules.py`: jede Regel liest ein Sample (dazu den Rundentracker und die Sitzungsereignisse) und
   kann einen `Alert` zurückgeben (Regel-ID, Priorität, Kurztext, die Werte, die ihn ausgelöst haben).
   Regeln sprechen nie selbst.
2. `engineer/engine.py` (`Engineer`) wendet der Reihe nach an: eine stumme Phase (Menü, Replay, Pause) oder
   die ersten `startup_quiet_s` nach Sitzungsbeginn; die Sitzungsrichtlinie (Training: alles; Qualifying:
   mittel und höher; Rennen: nur kritische Meldungen plus `lap_summary`, `fuel_low`, `penalty`); dann die
   Zustellung.
3. **Kritische** Alarme (Dreher, Reifenschaden, Sprit fast leer, schwarze Flagge) werden sofort gesagt und
   unterbrechen, was gerade gesprochen wird. Alles andere wartet auf einen *ruhigen Moment* (Bremse < 0,10 und
   |Lenkung| < 0,15 für 0,3 s, oder das Auto steht), wird verworfen, wenn es älter als seine TTL ist, und
   hält mindestens `calm.min_gap_s` Abstand zur vorigen Meldung.
4. `voice/speech.py`: ein Thread und eine Warteschlange. `say()` kehrt sofort zurück; eine kritische Meldung
   verwirft die wartenden. Eine defekte Stimme wirft nie eine Ausnahme in die Telemetrieschleife.

Das Meldungsformat ist fest: *Tatsache + Handlung*, im Stil des F1-Funks („Blockieren vorne links, Bremse
lösen“, „Sprit für 2 Runden, sparen“). Standard ist Spanisch; `language: de` oder `en` stellt die Sprache um.

## Protokollierung (Kalibrierung)

Jeder ausgelöste Alarm wird protokolliert (`alert fired`) mit seiner Regel und den Werten, die ihn ausgelöst
haben, jeder gesprochene (`alert said`, mit der Wartezeit) und jeder verworfene (`alert dropped`, mit dem
Grund: `policy:race`, `muted`, `expired`). Die Stimme nennt nie die Regel; das Protokoll tut es. Nutze
`--log-level INFO --plain` oder `logging.file` für JSON-Zeilen.

## Regeln

| Regel | Löst aus, wenn | Standard | Anmerkung |
|---|---|---|---|
| `lockup` | ein Rad bei < 0,70 der Autogeschwindigkeit, während Bremse > 0,4 über 50 km/h für 0,25 s | an | Schwelle aus einem echten Rennen: schlechtestes Rad p1 0,65, p5 0,77, Median 0,91 (MX-5 Cup) |
| `wheelspin` | angetriebene Räder > 1,30 der Autogeschwindigkeit bei > 0,7 Gas | aus | gesprächig |
| `spin` | Winkel zwischen Fahrzeuglängsachse und Bewegungsrichtung > 60° bei > 30 km/h | an | kritisch; bei einem echten Dreher gesehen |
| `offtrack` | 3+ Räder für 1 s außerhalb | an | von der Rennrichtlinie verworfen |
| `fuel_low` | Sprit für ≤ 2 Runden (kritisch ≤ 0,8) | an | braucht 2 gezeitete Runden Verbrauch |
| `tyre_hot` | Kerntemperatur > 115 °C für 3 s, wieder scharf unter 105 °C | an | **nicht je Mischung kalibriert** |
| `tyre_pressure` | > 40 psi, oder Abfall > 4 psi in 3 s (Reifenschaden) | an | |
| `brake_hot` | Bremstemperatur > 800 °C | an | wird automatisch ignoriert, wenn der Sensor sich nicht bewegt (Supra GT4) |
| `penalty` | `penalty_time` steigt | an | Sekunden, die noch abzusitzen sind: 10,0 bei einer 10-s-Strafe, danach in Echtzeit herunterzählend (17.10.2026) |
| `flags` | die Flagge ändert sich | **aus** | live gesehen: 5 = Zielflagge (Ende von Qualifying und Rennen), 6 = schwarz-weiße Strafflagge (erschien bei einer 10-s-Strafe, nicht angesagt: das macht die Strafregel); blau und gelb noch nicht gesehen, das Safety Car gibt es nur online mit Real Penalty |
| `damage` | die Summe der Schadenszonen steigt um `min_increase` | an | bei echten Kontakten gesehen: Zonen in km/h des Aufpralls (7 bis 40); im Rennen erlaubt |
| `rival_close` | im Rennen liegt das Auto hinter (oder vor) dir innerhalb von `gap_s` und der Abstand ist in 30 s um `min_closing_s` geschrumpft | **aus** | braucht die CSP-Brücke (`tools/csp_bridge`); Abstände aus dem Positionsverlauf gemessen |
| `rival_pit` | das Auto vor dir in der Wertung fährt in die Boxengasse | **aus** | braucht die Brücke |
| `csp_flags` | die von CSP gemeldete Flagge ändert sich (gelb, blau mit Namen des Autos, schwarz, Strafe, weiß, Zielflagge) | **aus** | braucht die Brücke; aus, bis jede Flagge live gesehen wurde |
| `lap_summary` | eine Runde ist beendet | an | Zeit und Delta zur besten |
| `best_sector` | ein Sektor ist eine Bestzeit | aus | gesprächig |

Alle Schwellen stehen kommentiert in `config/default.yaml`. `pit-wall rules` zeigt, was aktiv ist.

## Gemessene Latenz

* Sample → Aufruf von `backend.speak()` (Software, 30 Samples durch Sitzungs-Engine, Regeln und
  Warteschlange): unter den 100 ms, die der Test prüft; der Sprachthread fügt < 20 ms Wartezeit hinzu
  (tests/test_engineer.py).
* `backend.speak()` → erstes Audio mit Windows SAPI, Stimme Sabina (es-MX), gemessen mit `pit-wall say`:
  242 ms beim ersten Mal (kalt), danach 110 / 89 / 101 ms. Das Ziel von < 200 ms für Regelalarme wird
  gehalten, sobald die Stimme warm ist; die erste Meldung eines Laufs ist langsamer.

## Noch nicht verifiziert

Die blaue, gelbe, schwarze und weiße Flagge, welche Zone welche ist bei den Schadenswerten,
Reifentemperaturfenster je Mischung und `brake_temp` bei Autos ohne den Sensor.
