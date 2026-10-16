# Pendientes del autor

Lista viva de lo que **tiene que hacer una persona** (no el código). Se actualiza cuando algo se cierra.
Última actualización: 2026-10-16.

El orden es por lo que más desbloquea. Lo que ya está hecho está al final, en "Ya cerrado".

## 1. Una sentada de mediciones  ·  guion en `docs/SESION_MEDICION.md`

* **FPS con el reconocedor de voz**: la voz ya funciona en pista con la GPU y el juego limitado a unos 60 FPS
  (reconocimiento de 0,6 a 2 s con `small`, 0,8 a 1 s con `medium` en el menú). Falta la tabla de FPS: tres
  bloques de 3 vueltas, misma pista y coche, anotando FPS medio y mínimo. Con ella se decide `stt.model`
  (`small`, `medium`, `turbo`) y si conviene limitar los FPS del juego.

  | Bloque | En paralelo | FPS medio | FPS mínimo |
  |---|---|---|---|
  | 1 | nada (juego solo) | ? | ? |
  | 2 | `pit-wall voice --stt-device cpu` | ? | ? |
  | 3 | `pit-wall voice --stt-device cuda --stt-model medium` | ? | ? |
  | 4 | `pit-wall voice --stt-device cuda --stt-model turbo` | ? | ? |

* **Eventos del juego**: el puente CSP (5l) ya envía banderas, penalización y daño; falta **provocarlos y
  grabar con `pit-wall record`** (apunta la hora): bandera amarilla, azul, blanca y negra, límites de pista y
  salida en falso, un contacto con daño y safety car. Con eso se encienden las reglas con datos reales (hoy
  apagadas porque nunca se ha capturado un evento). Si usas Real Penalty, su log de la sesión.

## 2. Voz: lo que falta de probar  ·  `docs/VOICE.md`

Hecho el 2026-10-16: micrófono (Razer Seiren Mini), voz (Sabina), botón del volante (el 10, como joystick; el 9
quedó descartado), conversación en vivo con el juego, y la pronunciación de "psi". Falta:

* **Frases habladas** (`docs/FRASES_VOZ.md`): las 40 en español, en silencio y con el coche sonando (con el
  micrófono o con el móvil, en `frases/telefono/`). Con ellas se cierra el paso 5 de la voz y se decide el modelo
  con datos. Mientras tanto se van corrigiendo los fallos que salgan hablando (hoy: "desgaste", "pits",
  "Magione"); pásame lo que oiga mal.
* **Conducir con la voz**: una carrera completa con `pit-wall voice` (ahora solo se ha probado en boxes y en
  vueltas de calentamiento): ¿se oye bien con el motor?, ¿molestan las alertas mientras hablas?, "no molestes".

## 3. Idiomas (alemán e inglés)

* Instalar una voz alemana de Windows (Configuración > Hora e idioma > Voz > Agregar voces > "Deutsch
  (Deutschland)", trae Hedda y Katja) y elegirla con `pit-wall voices --test`.
* Poner `engineer.language: de` (o `en`) en `config/local.yaml` y leer las frases de `docs/FRASES_VOZ.md`;
  decirme si el reconocimiento en alemán es suficiente y qué preguntas no entiende el enrutado local.

## 4. Seguridad y decisiones

* **Rotar la clave de Gemini.** Las dos claves que has usado quedaron escritas en la conversación. Entra a
  Google AI Studio > API keys, borra las usadas y crea una nueva solo para el proyecto; ponla en `.env` como
  `GEMINI_API_KEY=...` (`.env` está ignorado por git).
* **Claves de reserva**: Gemini devuelve a menudo 503 ("high demand") y timeouts (2026-10-16) y el ingeniero cae al
  texto de respaldo. Una segunda clave (`GEMINI_API_KEY_2` o `GEMINI_API_KEY_LIST=a,b,c`) o un proveedor de
  reserva (`docs/PROVIDERS_PLAN.md`) lo reduce. Revisa los términos de Google antes de usar muchas cuentas
  gratuitas. El timeout bajó a 3,5 s (`llm.timeout_s`); si se pierden respuestas buenas, súbelo a 4,5.
* **Disco C: lleno** (quedan unos 800 MB): Windows y el juego escriben ahí. Los modelos de reconocimiento ya van
  a `E:\Documents\pit-wall-models` (`stt.model_dir`); libera algo más en C:.
* **Aprobar el plan de carrera (fase 8a)** y el resto de la fase 8 antes de empezar: `docs/PHASE8_PLAN.md`.

## 5. Mensajes de pole y de resultado (nuevo)

* una **qualy en la que salgas primero** y una **carrera por vueltas** que termines (idealmente subiendo
  puestos): grábalas con `pit-wall record` y anota qué dijo el ingeniero; así se comprueban la pole
  provisional, la victoria y el final de carrera;
* una **carrera por tiempo** (sin número de vueltas): hoy el final no se detecta; con una grabación se añade.

## 5b. Memoria del ingeniero

* tras unas sesiones en vivo, pregunta "¿qué me dijiste de la curva 4 la última vez?" o "¿qué me dijiste de la
  gasolina?" y dime si la respuesta tiene sentido; el archivo es `.pit-wall-ai/memory.jsonl` en tu carpeta de
  usuario (borrarlo olvida todo; `engineer.memory.enabled: false` lo apaga).

## 5c. Planes nuevos que esperan tu respuesta (2026-10-15)

* **Debrief post-sesión** (`docs/PHASE7_PLAN.md`): aprobar el plan y contestar sus 4 preguntas (sección 7).
* **Voces neuronales, perfiles y efecto de radio** (`docs/VOICES_PLAN.md`): aprobar el plan, y decidir si
  Piper en CPU por defecto te sirve y si quieres el efecto de radio con intensidad ajustable. Recuerda la
  línea del plan: el proyecto no incluye ni nombra voces de personas reales; los perfiles zero-shot usan un
  clip que tú aportas y tienes derecho a usar.
* **Motor de estrategia** (`docs/PHASE8_PLAN.md`, sección 4b): combustible, ventana de parada con tráfico y
  clima; sin dependencia del safety car. Pide que midamos la pérdida en pit lane con tus paradas reales.
* **Visor remoto de muro abierto** (`docs/MULTI_SIM_PLAN.md`, sección 9): idea; decide si merece una fase.
* ~~**README en alemán**~~ (2026-10-15): `README.de.md` y `docs/VOICE.de.md` hechos, con el estado del
  proyecto actualizado en los tres README. Léelos tú: una revisión de un hablante nativo (o tuya) antes de
  usarlos en una candidatura es lo que da la última pulida. Si quieres más documentos en alemán, dime cuáles.

## 5h. Imola verificado (2026-10-15)

Con tu mapa (Wikimedia Imola_2009): 18 curvas en el layout de coche; la Variante Bassa (19) es de moto y no
se usa. 1 sin nombre; Tamburello 2, 3, 4; Villeneuve 5, 6; Tosa 7; Piratella 9; Acque Minerali 11, 12, 13;
Variante Alta 14, 15; Rivazza 1 = 17 y Rivazza 2 = 18. Las 15 apuestas de la línea quedan con su número
(el 16 es la curva suave a derechas antes de Rivazza); 1, 8 y 10 son curvas planas sin ápice en la línea.
Imola está marcado como **numeración verificada**: el ingeniero dice "curva 14" con el número de tu mapa.
Siguiente circuito cuando quieras (Monza con tu mapa de 8 curvas, y el Serraglio como la 6).

## 5i. Spa-Francorchamps verificado (2026-10-15)

Con tu lista sobre el SVG: 1 La Source; 2 sin nombre; 3 Eau Rouge; 4-5 Raidillon; 6 sin nombre; Kemmel
Straight; 7-8 Les Combes y 9 Malmedy (en algunos mapas la 9 es de Les Combes); 10 Bruxelles; 11 Speaker's
Corner; 12 Pouhon; 13-14 Les Fagnes (también dibujadas como Campus); 15 Stavelot; 16 sin nombre; Courbe Paul
Frère (la curva larga a derechas, sin número propio); 17 Blanchimont; 18 sin nombre; 19-20 Bus Stop. La
línea tiene 18 ápices: 2, 6 y 18 no tienen ápice (son planas) y la Courbe Paul Frère es un ápice con
nombre y sin número. Marcado como **numeración verificada**.

## 5j. Red Bull Ring verificado (2026-10-15)

Con tu mapa de 10 curvas (layout GP): 1 Niki Lauda Kurve; 2 sin nombre; 3 Remus; 4 Schlossgold; 5 sin
nombre; 6 Rauch; 7 Würth Kurve; 8 sin nombre; 9 Rindt; 10 Red Bull Mobile. La línea tiene 8 ápices: la 2 (la
curva suave de la subida) y la 5 (la izquierda rápida tras Schlossgold) son planas y no tienen ápice. El
sentido de los 8 ápices (D D D I I D D D) coincide con el de las curvas 1, 3, 4, 6, 7, 8, 9 y 10 del mapa.
Marcado como **numeración verificada**. Si alguna asignación no te cuadra, dímelo.

## 5k. Trazados y Silverstone verificado (2026-10-15)

La tabla de circuitos distingue ahora el trazado (`layouts`): un trazado cuya longitud no coincide con la de la
tabla (±8 %) no recibe nombres de otro; los inversos reutilizan los nombres con el orden invertido (derivado,
sin verificar). **Red Bull Ring national** (6 curvas: Niki Lauda, Würth, Rindt, Red Bull Mobile; 2 y 4 sin
nombre) y **Silverstone** con tus mapas: GP con 18 curvas (Maggotts 10-11, Becketts 12-13, Chapel 14, Stowe 15,
Vale 16-17, Club 18; dos curvas ligeras sin número), International (8) y National (7). Pendiente: confirmar en
el juego que en el National el 3 y el 4 son la misma horquilla, y el mapa de los inversos de cada circuito.

## 5l. Puente CSP: banderas, penalización y daño (el desgaste ya está)

El desgaste real ya llega y coincide con tu app CMRT (99/98/69/62 % contra 99,7/98,8/69,9/62,3 %). El mismo
mensaje trae bandera, penalización, daño por zona y lluvia, que nadie usa todavía: ver "Eventos del juego" en la
sección 1. Sin el puente (app no activada o juego cerrado) el ingeniero no dice el desgaste, no lo inventa.

**Rivales (versión 0.2 del puente, 2026-10-16, sin probar en el juego)**: la app envía también todos los coches
(posición, vueltas, distancia en la vuelta, tiempos, sectores, boxes, compuesto) y los datos de la sesión; pit-wall
calcula los intervalos en segundos y contesta "¿quién tengo delante / detrás y a cuánto?", "mis rivales", "¿cómo va
el de delante en sectores?". Para probarlo: volver a copiar la app (`pit-wall bridge-install`; ya está copiada),
reiniciar AC, y con una carrera contra IA abierta `pit-wall bridge-test`: debe imprimir una línea `rivals:` con
los coches, tu posición y los de delante y detrás. Si no sale o los intervalos no cuadran con la app CMRT (a ±0,2 s),
pásame la línea. Los campos de CSP se leyeron de su documentación, sin ver el juego: es lo más probable que falle.

## 5e. Verificar la numeración de los circuitos

Verificados con tu mapa: Magione, Imola, Spa, Monza, Red Bull Ring (GP y national) y Silverstone (GP,
International y National). **Faltan los mapas con números** de Mugello, Laguna Seca, Brands Hatch, Barcelona,
Zandvoort, Vallelunga, Nordschleife, Le Mans, Mónaco y Sepang (Oran Park y Lime Rock no tienen línea en tu
instalación). Para cada uno necesito el mapa con los números y los nombres; los demás trazados (national,
inverso) con su mapa si lo tienes. `pit-wall circuit-maps` genera el dibujo local de la línea para comparar.

## 5f. Lluvia al final de la carrera (planificación)

Añadido a `docs/PHASE8_PLAN.md` (4b.1): con pocas vueltas por delante la parada solo compensa si
`vueltas que quedan x ganancia por vuelta > pérdida en boxes + margen`; la simulación de Monte Carlo da la
probabilidad de que parar sea mejor que quedarse, y solo una **situación peligrosa medida** (pérdida de
tiempo, deslizamientos y salidas de pista, agarre estimado, campo de lluvia del juego) la vuelve urgente.
Para construirla necesito: una grabación con una carrera o práctica en que empiece a llover y pares, tu
pérdida en pit lane en algún circuito, y revisar qué campos de lluvia da cada simulador.

## 5g. Calibración de lluvia (cuando quieras, sin prisa)

* graba las series de `docs/SESION_MEDICION.md` 3b (seco, lluvia ligera con slicks y con mojados, lluvia
  fuerte con ambos) con unos pocos coches y circuitos; con ello el motor de estrategia tendrá una tabla
  de partida para circuitos donde nunca ha llovido (`docs/PHASE8_PLAN.md` 4b.2);
* anota qué campos de lluvia o humedad ves en el juego (Content Manager) por si el `probe` los muestra.

## 5d. Nombres de curvas (nuevo)

Añadidos el 2026-10-15 a `data/circuits.json`, con la posición medida en la línea de tu instalación:
**Monza** (12 curvas, de la Variante del Rettifilo a la Parabolica), **Mugello** (completo, 15) y **Laguna
Seca** (Andretti Hairpin, Corkscrew 1 y 2, Rainey Curve). Ninguno está marcado como verificado: para que el
ingeniero diga "curva N" con el número del circuito hace falta que compruebes la numeración contra el mapa
real (`pit-wall corners --track monza --length 5793` y el plano de la pista). **Recta principal y recta trasera** (idea tuya, 2026-10-15): se calculan solas para cualquier circuito con
línea de carrera (no hace falta tabla): la principal es el hueco entre curvas que contiene la línea de
meta (mínimo 250 m) y la trasera es el hueco más largo de los demás (mínimo 400 m); una recta con nombre
tiene prioridad. Medidas en tu instalación: Monza 1 192 m y 952 m, Imola 1 186 m y 462 m, Mugello 1 158 m,
Laguna Seca 464 m (la trasera es la Rahal), Brands Hatch 300 m y 566 m. Revisa que coincidan con lo que tú
llamas recta principal / trasera en tus circuitos.

**Monza verificado con tu mapa de 11 curvas** (2026-10-15, Wikimedia Monza_track_map): 1-2 Variante del
Rettifilo (Prima Variante); 3 Curva Grande (Biassono; son dos ápices en la línea); 4-5 Variante della Roggia
(Seconda Variante); 6-7 Curve di Lesmo; 8-10 Variante Ascari; 11 Curva Parabolica. La Curva del Serraglio
(la recta trasera) no lleva número y la recta principal se llama Rettifilo Tribune; el tramo de Ascari a la
Parabolica, Rettifilo Centrale. El mapa de 8 curvas agrupa las chicanes en una sola; el ingeniero usa el de 11.

**Rectas con nombre** (idea tuya, 2026-10-15): `data/circuits.json` admite `straights` (de qué fracción
de la vuelta a cuál); hay Rahal Straight (Laguna Seca), Kemmel Straight (Spa) y Hangar Straight
(Silverstone), y el chat contesta "¿qué es la recta Rahal?" diciendo entre qué curvas va. Comprueba que
la recta empieza y acaba donde tú la conoces; si me das las de otros circuitos las añado igual.
Si me dices cuáles
circuitos usas más, completo esos primero (los que dejé sin nombres porque la línea del juego no coincide
con la tabla: Zandvoort, Vallelunga; con dudas sobre el orden: Red Bull Ring, Barcelona, Brands Hatch).

## 5m. Lo que falta del lado del código (sin ti, o con una prueba tuya al final)

* **Reglas de banderas, daño y penalización** con los datos del puente: necesitan las capturas de la sección 1.
* **Alertas de rivales** (se acerca el de atrás, el de delante entra a boxes, bandera azul con el nombre de quien la
  provoca, última vuelta del líder) y **final de carrera por tiempo** con `session.over`: los datos ya llegan en
  el puente 0.2; faltan las reglas, que conviene escribir cuando se vea cómo llegan en una carrera real.
* **Índice de desgaste por curva en vivo** con el porcentaje real del puente: el análisis ya existe (`tyre-corners`);
  falta usarlo en la conversación en vivo y compararlo con tu app.
* **Estimación del desgaste cuando el simulador no lo da** (iRacing: solo al parar): plan en `PHASE8_PLAN.md` 3.5.
* **Final de carrera por tiempo**: hoy no se detecta; necesita una grabación (sección 5).
* **Motor de estrategia / decisión de parada** (fase 8): hoy "¿debería entrar a pits?" contesta solo el desgaste
  medido; el plan espera tu aprobación (sección 4).
* **Debrief** (fase 7) y **voces con efecto de radio**: esperan tu aprobación de los planes (sección 5c).
* **Más traducciones al alemán** y más correcciones del reconocimiento a medida que salgan fallos hablando.

## 6. Opcional, sin prisa

* **Una tanda manual variando** (6 a 8 vueltas, bloqueando, con mucho volante y con gas brusco) con captura de
  la app de neumáticos al principio y al final: da un tercer punto de validación del desgaste por curva, con
  tu forma de conducir.
* **Una grabación pequeña como fixture** de la sesión del Cobra, si quieres que entre al repositorio:
  `pit-wall trim --file sesion3.pwrec.gz --from 0 --to 600 --rate 10 --out tests\fixtures\live_magione_cobra427_window.pwrec.gz`
  (el nombre debe ser real, sin `<` ni `>`).
* **Modo MCP** (aplazado, `docs/MCP_PLAN.md`): cuando se retome, necesito la documentación de tus MCP, el
  cliente que usarías y el valor por defecto de `mcp.local_voice` y de `mcp.local_voice_always`.

## Ya cerrado

* ~~Voz en vivo con tu equipo~~ (2026-10-16): micrófono Razer Seiren Mini, voz Sabina, botón 10 del G29 como
  joystick, reconocimiento en la GPU (`stt.device: cuda`; `small`, `medium` y `turbo` configurables con
  `stt.model` o `--stt-model`), "psi" dicho letra a letra, y las preguntas sobre un neumático, un eje o una sola
  medida contestadas solo con eso.
* ~~Desgaste real de los neumáticos~~ (2026-10-16): puente CSP (`tools/csp_bridge`, `pit-wall bridge-install` y
  `bridge-test`); coincide con la app CMRT. En iRacing el desgaste solo se conoce al parar: ver
  `docs/PHASE8_PLAN.md` 3.5.
* ~~Trazados y circuitos~~ (2026-10-15): tabla sensible al trazado (national, inversos), Red Bull Ring national y
  Silverstone GP, International y National verificados; el ingeniero lista los nombres de las curvas.
* ~~Mensaje de respaldo cuando el modelo falla~~ (2026-10-16): sin el error técnico ni las partes vacías.
* ~~Segunda sesión grabada y evaluación del chat~~ (2026-10-15): sesiones con el Shelby Cobra 427 SC en
  Magione; el chat evaluado sobre `sesion3` (13 vueltas, otro coche y otro compuesto): **29 de 30** casos, 17
  respuestas del modelo sin afirmaciones eliminadas, mediana 2,2 s (p95 4,1 s); el único fallo era un caso mal
  planteado (exigía una cifra concreta del último aviso) y ya está corregido.
* ~~Desgaste de neumáticos por curva~~ (2026-10-15): el canal de la memoria compartida no se mueve en esta
  configuración, pero con la app de neumáticos del juego como referencia el indicador por rueda coincide con
  el desgaste real en dos sesiones (correlación 1,00 con tu conducción y 0,99 con el piloto automático, mismo
  orden de las cuatro ruedas): `pit-wall tyre-corners sesion3.pwrec.gz --reference 83,88,70,75`. El
  ingeniero ya contesta "¿en qué curvas gasto más los neumáticos?" como estimación, con la causa y el consejo.
* ~~Modelo en la nube probado~~ (2026-10-14): `llm-check` 3/3 y `pit-wall eval` 34/34, estilos de la persona
  verificados, tiempos de vuelta como minutos y segundos.
* ~~Voz de punta a punta en simulación~~ (2026-10-14): micrófono, reconocimiento local (CPU 1,9 s y GPU 0,4 s
  por pregunta), reglas de radio, modo "no molestes" y órdenes habladas.
* ~~Idiomas~~ (2026-10-14): español, inglés y alemán en alertas, respuestas locales, modelo, órdenes,
  memoria y reconocimiento.
* ~~Varias claves de API~~ (2026-10-14): reserva en orden, con la comprobación de cada clave.
* ~~Decisiones de la voz~~: gana el que empieza primero, los críticos siempre salen, solo se dicen los
  eventos críticos e importantes, el botón se asigna con `pit-wall bind`.
* ~~"psi" mal reconocido~~: tabla de correcciones en `stt/postfix.py`; se amplía con tus frases reales.
* ~~"¿Cuánto tiempo queda?" en carrera sin límite~~: dice que la sesión no tiene límite de tiempo.
* ~~Recortar una grabación a un tamaño publicable~~: `pit-wall trim`.
