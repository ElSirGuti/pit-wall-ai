# Sesión única de mediciones (guion)

Todo lo que falta medir con tu PC, el coche y el volante, ordenado para hacerlo **en una sola sentada**
(unas 2 horas con descansos). Cada bloque dice qué se hace, qué comando, y para qué sirve. Al final, qué
hago yo con los archivos.

Marca `[x]` según avances y guarda este archivo con tus notas (sin subirlo si tiene datos personales).

## 0. Antes de abrir el juego (15 min)

* [ ] `git pull` y `.venv\Scripts\pip install -e ".[voice,stt,stt-cuda]"`.
* [ ] `pit-wall devices`: anota el micrófono (casco o Seiren) y la salida (casco) que quieres usar.
* [ ] `pit-wall mic-test --device <nombre> --seconds 5`: el nivel debe pasar de 5 % y no recortar (clip).
* [ ] `pit-wall voices --test --device <salida>`: elige la voz que te guste (o instala otra y repite).
* [ ] Pon las decisiones en `config/local.yaml`: `audio.input_device`, `engineer.voice.name`,
  `engineer.voice.output_device`.
* [ ] G HUB abierto: `pit-wall bind push_to_talk` (pulsa el botón del volante) y `pit-wall input-test` con una
  pulsación larga y una corta.

## 1. Frases habladas, tanda A: en silencio (15 min)

* [ ] Las 40 de `docs/FRASES_VOZ.md`, un WAV cada una en `frases/A/NN.wav`.
* [ ] `pit-wall stt-test frases/A/*.wav --device both` (anota error por palabras y latencia).

## 2. Carga del reconocedor y fps del juego (15 min)

Una sola vuelta de prueba con el juego en la pista elegida; el fps se lee con el contador que ya uses
(Steam, Game Bar, CapFrameX...). Tres bloques de 3 vueltas cada uno, misma pista y coche:

| Bloque | Qué corre en paralelo | Anota |
|---|---|---|
| 1 | nada | fps medio y mínimo |
| 2 | `pit-wall stt-test frases/A/*.wav --device cpu --repeat 30` en otra ventana | fps medio y mínimo |
| 3 | lo mismo con `--device cuda` | fps medio y mínimo |

* [ ] Con eso se decide `stt.device` para este juego (y se repite por juego: iRacing, etc.).

## 3. Sesión de pista para todo lo demás (45 a 60 min)

Un solo archivo: `pit-wall record --out sesion_medicion.pwrec.gz` empezando **antes** de salir del pit y
terminando al volver. Para que sirva a todo a la vez:

**Condiciones del coche**
* [ ] Un coche con desgaste de neumáticos apreciable (no el MX-5 Cup: en tus grabaciones el desgaste solo se
  movió 0,25 puntos en 9 vueltas). Si el juego tiene la opción de desgaste de neumáticos en las ayudas o en
  las opciones de la sesión, ponla en normal o acelerada.
* [ ] Un solo compuesto, **12 vueltas seguidas** o más.

**Qué provocar (apunta la vuelta en la que lo hiciste)**
* [ ] 3 vueltas limpias, seguidas, a ritmo normal.
* [ ] 2 vueltas frenando tarde a propósito para bloquear los delanteros.
* [ ] 2 vueltas metiendo mucho volante / pasándote de velocidad en curvas lentas (subviraje).
* [ ] 2 vueltas con gas brusco en la salida de las curvas lentas.
* [ ] 1 trompo y 1 salida de pista (ya hay detección de incidentes; sirven para comprobar que se excluyen).
* [ ] Una parada en boxes y volver a salir con el mismo compuesto.

**Banderas, daños y penalizaciones (en la misma sesión si es posible)**
* [ ] Amarilla y azul: carrera contra IA (déjate doblar una vez).
* [ ] Blanca (última vuelta) y cuadros.
* [ ] Penalización de AC nativo por límites de pista y por salida en falso (drive-through): una carrera corta
  con penalizaciones activadas; apunta cuál y a qué hora.
* [ ] Un contacto con daño (suspensión o carrocería).
* [ ] Si usas Real Penalty: guarda también su log de la sesión.
* [ ] Safety car, si algún modo lo ofrece.

Mientras tanto, **di en voz alta o apunta en el móvil la hora de cada cosa**: las banderas se localizan por
el valor que cambia en la memoria compartida y es mucho más rápido si sé el segundo aproximado.

## 3b. Calibración de lluvia (cuando quieras; sesión aparte)

Para que el motor de estrategia sepa cuánto se gana con mojados donde nunca ha llovido
(`docs/PHASE8_PLAN.md` 4b.2). Un coche y un circuito por serie; graba cada tramo con `pit-wall record` y
apunta qué hacías:

| Tramo | Qué | Vueltas |
|---|---|---|
| A | seco, limpio, mismo coche y circuito | 3 |
| B | lluvia **ligera y constante**, con slicks | 3 |
| C | la misma lluvia, con mojados o intermedios (la vuelta de calentamiento no cuenta) | 3 |
| D | lluvia **fuerte**, con slicks, hasta que sea inconducible (anota la vuelta) | 2 a 3 |
| E | la misma lluvia fuerte con mojados | 3 |

Si el simulador te deja fijar el nivel de lluvia (Content Manager con CSP lo permite), usa los mismos
niveles en todas las series. Cuantos más pares coche y circuito, mejor la tabla; con 3 o 4 ya sirve de
primera estimación.

## 4. Frases habladas, tanda B: con motor (10 min)

* [ ] En los boxes, coche con el motor a unas 4000 rpm sostenidas (o en la parrilla), micrófono en su sitio.
* [ ] Las 40 frases en `frases/B/NN.wav`; `pit-wall stt-test frases/B/*.wav --device both`.

## 5. Después: qué hago yo con los archivos

* Con `sesion_medicion.pwrec.gz` (recortado con `pit-wall trim` si pesa mucho):
  * `pit-wall tyre-corners sesion_medicion.pwrec.gz`: valida (o descarta) el indicador de desgaste por curva
    y, si pasa la validación, se habilita el consejo de neumáticos por curva;
  * tabla de banderas (qué valor de `graphics.flag` es cada una), daños y penalizaciones; se activan las
    reglas, cada una con su prueba hecha sobre la grabación;
  * evaluación del chat y de los avisos sobre una sesión larga.
* Con las frases: tamaño de modelo (`small` o `medium`), tabla de correcciones ("psi", números, nombres de
  curvas), y la latencia de extremo a extremo para el paso 4.
* Con los fps: valor por defecto de `stt.device` para este juego y aviso en la documentación.
* Todo se anota con cifras en `docs/PHASE6_PLAN.md` y `docs/PHASE8_PLAN.md`; los audios y las grabaciones no
  se suben (ignorados por git), solo lo recortado que tú apruebes.
