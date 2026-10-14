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

* [ ] tanda A (silencio)
* [ ] tanda B (con motor)
* [ ] cifras anotadas en `docs/PHASE6_PLAN.md`
