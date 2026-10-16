# CSP-Brücke (Pit Wall Bridge)

Eine Lua-App aus zwei Dateien für Assetto Corsa mit Custom Shaders Patch. Sie liest den Fahrzeugzustand über
die CSP-Lua-API und sendet ihn zweimal pro Sekunde an `127.0.0.1:28271`, wo `pit-wall` zuhört
(`telemetry.csp_bridge`).

Was sie sendet: den echten Reifenverschleiß je Rad (verbleibende Lebensdauer in %, wie in den Reifen-Apps),
geplatzte Reifen, die Reifenmischung, die Rennflagge und ihre Ursache, die aktuelle Strafe, die vier
Schadenszonen, den Getriebeschaden sowie Regen und Nässe der Strecke. Heute nutzt der Ingenieur nur den
Verschleiß; der Rest ist das, was die Regeln für Flaggen, Schaden und Strafen lesen werden, sobald diese
Ereignisse aufgezeichnet sind.

Sie liest nur, schreibt nie ins Auto und spricht ausschließlich mit dem lokalen Rechner. Installation mit
`pit-wall bridge-install` (kopiert `pitwall_bridge/` nach `<assettocorsa>/apps/lua/`); danach *Pit Wall
Bridge* einmalig in der App-Leiste des Spiels aktivieren. `pit-wall bridge-test` zeigt, was ankommt.

## Version 0.2: die anderen Autos

Neben dem Zustand des eigenen Autos enthält jede Nachricht `cars` (ein Eintrag je verbundenem Auto, bis zu
40: Rennposition, Runden, Position in der Runde, Geschwindigkeit, beste/letzte/aktuelle Runde, die Sektoren
der laufenden Runde, der letzten und der besten Runden, Box/ausgeschieden/im Ziel, Gültigkeit der Runde und
Reifenmischung) und `session` (Typ, Runden, Dauer, ob nach Zeit, vom Führenden absolvierte Runden, ob
beendet). `pit-wall` macht daraus die Abstände in Sekunden (Assetto Corsa liefert sie nicht: sie werden aus
Ort und Zeitpunkt der Autos gemessen, wie auf einer Zeitentafel) und beantwortet „Wer ist vor mir?“, „Wie weit
ist der Hintermann?“ und „Wie schlägt sich der Vordermann in den Sektoren?“. `pit-wall bridge-test` zeigt eine
Zeile mit den Gegnern, sobald sie ankommen.

## Ohne CSP

Ist die Python-App *CrewChiefEx* im Spiel aktiviert, liest `pit-wall` die Seite `acpmf_crewchief` und liefert
die Gegner auch ohne diese Lua-App (mit weniger Feldern: keine Sektoren, keine Mischung, keine Flaggen). Sendet
die Brücke, hält sich dieser Leser zurück.
