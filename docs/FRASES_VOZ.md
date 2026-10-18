# Frases habladas para medir el reconocimiento de voz

Para qué: medir con **tu voz y tu micrófono** lo que `pit-wall stt-test` solo midió con voz sintética
(`docs/PHASE6_PLAN.md` 7.1). Con esto se decide el tamaño del modelo, CPU o GPU, y qué correcciones de
vocabulario hacen falta ("psi", números, nombres de curvas).

## Cómo grabar

1. Elige el micrófono: `pit-wall devices` y `pit-wall mic-test --device <parte del nombre>`.
2. Graba **cada frase por separado**, una sola vez, con tu forma normal de hablar (como lo harías pilotando:
   ni despacio ni exagerado). Un archivo WAV por frase: `pit-wall mic-test --seconds 5 --out frases/01.wav`.
3. Haz **dos tandas**: A) coche parado, en silencio; B) con el coche sonando (en pista o con el juego
   abierto y el motor a medio régimen). Las frases marcadas con ★ son las más importantes para la tanda B.
4. Guarda los archivos en `frases/A/01.wav…` y `frases/B/01.wav…` (la carpeta `frases/` no se sube: está en
   `.gitignore` junto con los demás audios).
5. Para medir: `pit-wall stt-test frases/A/*.wav --ref "..." --ref "..." --device both`, o usa el texto de
   referencia de abajo en el mismo orden.

### Grabar con el teléfono

Sirve cualquier grabadora del móvil (notas de voz): el programa lee `.m4a`, `.aac`, `.mp3`, `.ogg`, `.opus`,
`.3gp` y `.wav` y los convierte solo. Nombra los archivos por el número de la frase (`06.m4a`) y déjalos en
`frases/telefono/` (dentro del repositorio, no se sube). Con el móvil a unos 20 a 30 cm, como un micrófono de
casco. Dime cuáles has dejado y los mido: `pit-wall stt-test frases/telefono/*.m4a --ref "..." --device both`.

## Las frases (referencia en el mismo orden que el número)

### Combustible y vueltas
1. ¿Cuánta gasolina me queda?
2. ¿Para cuántas vueltas me alcanza la gasolina?
3. ¿Cuántas vueltas quedan? ★
4. ¿Cuánto tiempo queda de sesión?
5. ¿Voy a llegar al final sin parar?

### Neumáticos
6. ¿Cómo tengo los neumáticos delanteros? ★
7. ¿Cómo están los neumáticos traseros?
8. ¿Cuál es la presión de los neumáticos en psi? ★
9. ¿Qué temperatura tienen los neumáticos?
10. ¿Cuál es el neumático más caliente?
11. ¿Cuánto se han desgastado los neumáticos?
12. ¿En qué curvas estoy gastando más los neumáticos? (función futura, ver `PHASE8_PLAN.md`)
13. ¿Cómo puedo cuidar los neumáticos delanteros?

### Frenos
14. ¿Cómo voy de frenos? ★
15. ¿Qué temperatura tienen los frenos?
16. ¿Estoy bloqueando mucho en las frenadas?

### Vueltas y tiempos
17. ¿Cuál fue mi mejor vuelta? ★
18. ¿Cuánto me falta para mi mejor vuelta?
19. ¿Cuál fue mi última vuelta?
20. ¿Cuánto perdí en la última vuelta?
21. ¿Cuántas vueltas válidas llevo?

### Curvas (Magione; cambia los nombres por los de tu circuito)
22. ¿Cuánto perdí en la curva seis?
23. ¿Qué tal fue la frenada de la curva cuatro? ★
24. ¿Dónde estoy perdiendo más tiempo?
25. ¿Cómo voy en la Curvone?
26. ¿Y en la Prima Esse?
27. ¿Qué me dijiste de la curva cuatro?
28. ¿Dónde debo frenar en la curva once?

### Sesión y penalizaciones
29. ¿Tengo alguna penalización?
30. ¿En qué posición voy?
31. ¿Qué compuesto llevo puesto?
32. ¿Cuántas vueltas llevo con este juego de neumáticos?

### Frases que no deben ser preguntas del chat (comandos y cortesía)
33. Repite lo último. ★
34. Entendido. (acuse de recibo)
35. Más corto, por favor.
36. Más despacio.
37. No te entendí, dilo otra vez.

### Casos difíciles (trampas, para ver cómo falla)
38. ¿Cuántos grados tiene el aire? (dato que no tenemos)
39. ¿Qué tal el clima en Monza?
40. Oye, ¿y mi compañero de equipo cómo va? (no existe)

## Qué se mide con ellas

* tasa de error por palabras (`stt-test --ref`), por tanda (silencio / motor) y por dispositivo (CPU / GPU);
* si "psi", los números (6 o "seis") y los nombres propios de curvas se reconocen;
* latencia de extremo a extremo cuando exista el paso 4;
* qué frases hay que ayudar con una tabla de correcciones o con vocabulario dirigido.

## Estado

* [x] tanda A (silencio), 2026-10-18, voz del autor, micrófono Razer Seiren Mini, GPU, con los nombres de las curvas de Magione como pistas:
  `small` 38 de 40 exactas, 1,4 % de palabras erróneas, 0,41 s por frase; `medium` 38 de 40, 0,9 %, 0,96 s; `turbo` 38 de 40, 0,9 %,
  1,21 s (y 153 s en cargar). Sin pistas de curvas y contando "seis" distinto de "6", `small` daba 32 de 40 y 4,2 %: la
  diferencia eran las cifras escritas con dígitos y los nombres propios. Fallos que quedaron: "cuántos se han
  desgastado", "prima ese", "di lo otra vez" y la "y" de la 40 (ya hay correcciones). En silencio no hay motivo para
  pasar de `small`.
* [ ] tanda B (con motor)
* [ ] cifras anotadas en `docs/PHASE6_PLAN.md`

## Frases en alemán y en inglés (para probar los idiomas)

Pon `engineer.language: de` (o `en`) en `config/local.yaml` y graba las frases en ese idioma. La
referencia es la misma numeración de arriba. Para la voz hace falta una voz de Windows del idioma
(Configuración > Hora e idioma > Voz > Agregar voces: "Deutsch (Deutschland)" trae Hedda y Katja;
`pit-wall voices` las lista).

### Deutsch
1. Wie viel Sprit habe ich noch?
3. Wie viele Runden sind noch übrig?
6. Wie sind die Reifen vorne?
8. Wie hoch ist der Reifendruck in psi?
14. Wie sieht es mit den Bremsen aus?
17. Was ist meine schnellste Runde?
18. Wie weit bin ich von meiner Bestzeit weg?
20. Wie viel habe ich in der letzten Runde verloren?
22. Wie viel habe ich in Kurve sechs verloren?
23. Wie war das Bremsen in Kurve vier?
24. Wo verliere ich am meisten Zeit?
27. Was hast du mir über Kurve vier gesagt?
29. Habe ich eine Strafe?
30. In welcher Position fahre ich?
33. Wiederhole das Letzte.
34. Verstanden.
35. Bitte kürzer.
36. Nicht stören!
37. Du kannst wieder sprechen.
38. Wie ist das Wetter in Monza? (Falle: keine Angabe vorhanden)

### English
1. How much fuel do I have left?
3. How many laps are left?
6. How are the front tyres?
8. What is the tyre pressure in psi?
14. How are the brakes?
17. What is my fastest lap?
20. How much did I lose on the last lap?
22. How much did I lose in corner six?
23. How was the braking in corner four?
24. Where am I losing the most time?
27. What did you tell me about corner four?
29. Do I have a penalty?
33. Repeat the last one.
36. Do not bother me now.
37. You can talk again.
38. What is the weather like in Monza? (trap: no such data)
