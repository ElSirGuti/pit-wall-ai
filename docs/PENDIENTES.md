# Pendientes del autor

Lista viva de lo que **tiene que hacer una persona** (no el código). Se actualiza cuando algo se cierra.
Última actualización: 2026-10-15.

El orden es por lo que más desbloquea. Lo que ya está hecho está al final, en "Ya cerrado".

## 1. Una sentada de mediciones  ·  guion en `docs/SESION_MEDICION.md`

Con el desgaste real ya medido (ver "Ya cerrado"), de aquella lista quedan:

* **FPS con el reconocedor de voz**: medir tres bloques de 3 vueltas, misma pista y coche, y anotar FPS medio y
  mínimo en cada uno. Ya tienes un dato: 142 FPS de media con `record` y `run --voice` corriendo.

  | Bloque | En paralelo | FPS medio | FPS mínimo |
  |---|---|---|---|
  | 1 | nada (juego solo) | ? | ? |
  | 2 | `pit-wall stt-test --device cpu --repeat 30` | ? | ? |
  | 3 | `pit-wall stt-test --device cuda --repeat 30` | ? | ? |

  Con eso se fija `stt.device` para Assetto Corsa.
* **Eventos del juego** (una grabación corta con `pit-wall record` alrededor de cada uno; apunta la hora):
  bandera amarilla, azul, blanca, negra y las penalizaciones de AC nativo (límites de pista y salida en falso);
  un contacto que cause daño; y safety car si algún modo lo ofrece. Si usas Real Penalty, el log de la sesión
  (`...\assettocorsa\apps\python\RealPenalty\logs\session_<fecha>.log`). Con eso se activan las reglas con
  datos reales (hoy están apagadas porque nunca se ha capturado un evento).

## 2. Voz: probarla con tu equipo  ·  `docs/VOICE.md`

* En casa, con `pip install -e ".[voice,stt]"`: `pit-wall devices`, `pit-wall mic-test --device <nombre>` y
  `pit-wall voices --test`; anotar el micrófono, la salida y la voz que prefieres y ponerlos en
  `config/local.yaml`.
* **El botón del volante**: con G HUB abierto, `pit-wall input-test --seconds 30` (pulsa botones y paletas del
  G29, una tecla y un botón del ratón), y después `pit-wall bind push_to_talk` y `pit-wall bindings`. Es lo
  único de la voz que no se ha podido probar sin ti.
* **Frases habladas** (`docs/FRASES_VOZ.md`): las 40 en español, en silencio y con el coche sonando, con el
  micrófono o con el móvil (en `frases/telefono/`). Con ellas se cierra el paso 5 de la voz y se decide si
  `small` basta o conviene `medium`.
* **Probar la conversación en vivo**: `pit-wall voice` (botón + micrófono) o al menos `pit-wall chat` con una
  práctica abierta; sobre todo "¿dónde pierdo tiempo?", "¿en qué curvas gasto más los neumáticos?" y
  "no molestes".

## 3. Idiomas (alemán e inglés)

* Instalar una voz alemana de Windows (Configuración > Hora e idioma > Voz > Agregar voces > "Deutsch
  (Deutschland)", trae Hedda y Katja) y elegirla con `pit-wall voices --test`.
* Poner `engineer.language: de` (o `en`) en `config/local.yaml` y leer las frases de `docs/FRASES_VOZ.md`;
  decirme si el reconocimiento en alemán es suficiente y qué preguntas no entiende el enrutado local.

## 4. Seguridad y decisiones

* **Rotar la clave de Gemini.** Las dos claves que has usado quedaron escritas en la conversación. Entra a
  Google AI Studio > API keys, borra las usadas y crea una nueva solo para el proyecto; ponla en `.env` como
  `GEMINI_API_KEY=...` (`.env` está ignorado por git).
* **Claves de reserva**: decidir cuáles poner (`GEMINI_API_KEY_2`... o `GEMINI_API_KEY_LIST=a,b,c`). Revisa
  los términos de Google antes de usar muchas cuentas gratuitas: las cuotas son por proyecto y los términos
  pueden prohibir abrir cuentas para sortear los límites. Una alternativa es otro proveedor como reserva
  (`docs/PROVIDERS_PLAN.md`). Con 2 o más claves, `pit-wall llm-check` debe listarlas como aceptadas.
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

## 5i. Spa-Francorchamps (propuesta, a la espera de tu confirmación)

20 números en tu mapa frente a 18 ápices en la línea: el 2 (salida de La Source) y el 6 (la curva
"Kemmel" al principio de la recta) son planos y no tienen ápice. En orden: 1 La Source; 3 Eau Rouge; 4
Raidillon; 5 (cima); 6 Kemmel; 7-8 Les Combes; 9 Malmedy; 10 Bruxelles; 11 Speaker's Corner; 12 Pouhon /
Double Gauche; 13-14 Les Fagnes; 15 Campus; 16 Stavelot; 17 Courbe Paul Frère (la larga a derechas de
Stavelot a Blanchimont); 18 Blanchimont; 19-20 Chicane (Bus Stop). Aplicada en la tabla pero **sin marcar
como verificada**: el chat ya entiende "curva 17", el ingeniero no dirá números hasta que confirmes.

## 5e. Verificar la numeración de los circuitos (nuevo, con tu idea)

1. `pit-wall circuit-maps` dibuja la línea de carrera de cada circuito de la tabla con sus ápices numerados,
   las rectas coloreadas y los nombres, y crea `circuit_maps\index.html` (local, no se sube: está en
   `.gitignore`). Orientación del juego, que puede estar girada respecto a un mapa publicado.
2. Abre el índice y, circuito por circuito, compara con tu mapa: ¿mismo número de curvas?, ¿mismo orden?,
   ¿izquierda y derecha coinciden?, ¿los nombres están donde deben?
3. Dime cuáles quedan bien y los marco `numbering_verified`; para los que agrupan chicanes en una sola
   curva (Monza en tu mapa: 8; la línea da 12) decidimos si añadimos una tabla ápice a número del mapa.
4. Los que no se dibujan (Oran Park, Lime Rock) no tienen línea en tu instalación.

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

**Monza, con tu mapa** (2026-10-15): la recta principal es *Rettifilo Tribune* y la trasera *Curva del
Serraglio* (el tramo de Lesmo 2 a la Variante Ascari). Ojo con **tres numeraciones distintas** del mismo
circuito: tu mapa agrupa en 8 curvas (1 Variante del Rettifilo, 2 Curva Grande, 3 Roggia, 4 y 5 Lesmo, 6
Serraglio, 7 Ascari, 8 Parabolica), la de la F1 usa 11 y la línea del juego da 12 ápices. Por eso Monza **no
está marcado como verificado** y el ingeniero dice los nombres, no "curva N". Si prefieres que hable con
la numeración de tu mapa (8), se añade una tabla ápice a número del mapa; dime si la quieres.

**Rectas con nombre** (idea tuya, 2026-10-15): `data/circuits.json` admite `straights` (de qué fracción
de la vuelta a cuál); hay Rahal Straight (Laguna Seca), Kemmel Straight (Spa) y Hangar Straight
(Silverstone), y el chat contesta "¿qué es la recta Rahal?" diciendo entre qué curvas va. Comprueba que
la recta empieza y acaba donde tú la conoces; si me das las de otros circuitos las añado igual.
Si me dices cuáles
circuitos usas más, completo esos primero (los que dejé sin nombres porque la línea del juego no coincide
con la tabla: Zandvoort, Vallelunga; con dudas sobre el orden: Red Bull Ring, Barcelona, Brands Hatch).

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
