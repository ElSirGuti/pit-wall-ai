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

## 5d. Nombres de curvas (nuevo)

Añadidos el 2026-10-15 a `data/circuits.json`, con la posición medida en la línea de tu instalación:
**Monza** (12 curvas, de la Variante del Rettifilo a la Parabolica), **Mugello** (completo, 15) y **Laguna
Seca** (Andretti Hairpin, Corkscrew 1 y 2, Rainey Curve). Ninguno está marcado como verificado: para que el
ingeniero diga "curva N" con el número del circuito hace falta que compruebes la numeración contra el mapa
real (`pit-wall corners --track monza --length 5793` y el plano de la pista). Si me dices cuáles
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
