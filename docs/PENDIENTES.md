# Pendientes del autor

Lista viva de lo que **tiene que hacer una persona** (no el código). Se actualiza cuando algo se cierra.
Última actualización: 2026-10-17 (todo lo aprobado, construido).

El orden es por lo que más desbloquea. Lo que ya está hecho está al final, en "Ya cerrado".

## 1. Una sentada de mediciones  ·  guion en `docs/SESION_MEDICION.md`

Hecho el 2026-10-17: un fin de semana completo con el Maserati GT4 en Magione (práctica, qualy, carrera de 8
vueltas) grabado en `sesiones/maserati_*.pwrec.gz`. De ahí salió: la bandera a cuadros (código 5), la bandera de
penalización (código 6) con una penalización de 10 s que baja en tiempo real (unidad verificada: segundos por
cumplir), daños reales (de 2 a 40 km/h de golpe) y el desgaste por curva validado (correlación 0,98). Falta:

* **FPS con el reconocedor de voz**: la tabla, con un juego abierto y 3 vueltas por bloque (FPS medio y mínimo).
  Con ella se decide `stt.model` y si conviene limitar los FPS.

  | Bloque | En paralelo | FPS medio | FPS mínimo |
  |---|---|---|---|
  | 1 | nada (juego solo) | ? | ? |
  | 2 | `pit-wall voice --stt-device cpu` | ? | ? |
  | 3 | `pit-wall voice --stt-device cuda --stt-model medium` | ? | ? |
  | 4 | `pit-wall voice --stt-device cuda --stt-model turbo` | ? | ? |

* **Eventos que no han salido todavía**: banderas azul y amarilla (la negra y la blanca son la bandera de
  penalización, código 6, que salió al sancionarte 10 s: dato del autor), el safety car (solo existe con Real
  Penalty en línea) y la salida en falso. Y saber qué zona de daño es cada una (hoy son 5 números, el 4.º parece el
  máximo de los demás): un golpe de lado conocido (por ejemplo, solo contra el muro derecho) lo aclara. Todo esto
  sale mejor **en línea (LFM)**: banderas con tráfico real, safety car y carreras por tiempo. Graba con
  `pit-wall record` y apunta la hora; el ingeniero ya los recibe por la API de CSP.

## 2. Voz: lo que falta de probar  ·  `docs/VOICE.md`

Hecho: micrófono (Razer Seiren Mini), voz Sabina, botón 10 del G29, conversación en vivo durante una práctica, una
qualy y una carrera de 8 vueltas con el reconocimiento en la GPU (0,6 a 1,2 s), "psi" y los números con coma bien
dichos (el 2026-10-17 arreglé "14,0 metros" que se leía como una hora). Falta:

* **Frases habladas** (`docs/FRASES_VOZ.md`): la **tanda A (silencio) está hecha** (2026-10-18: `small` 38 de 40, 1,4 %, 0,41 s;
  `medium` y `turbo` igual de exactas y 2 a 3 veces más lentas; se queda `small`). Falta la **tanda B con el motor
  sonando** (`--set B`), que es la que puede cambiar la decisión. Es guiado:
  `pit-wall phrases record --set A` (Enter para empezar, Enter para parar; `--set B` con el motor, `--only 3,6-8`) y
  `pit-wall phrases test --set A --device cuda` las mide (`--model medium` o `turbo` para comparar,
  `--dir frases/telefono` para las del móvil; también `--lang de` y `--lang en`). Con ellas se cierra el paso 5 de
  la voz y se decide el modelo con datos.
* **Fallos de reconocimiento que vimos** (pásame los que vayas notando): "desgaste", "pits", "Magione" ya corregidos;
  sin arreglo por tabla: "me limaliaron", "Que tal les va a estarte adelante".

## 3. Idiomas (alemán e inglés)

* Instalar una voz alemana de Windows (Configuración > Hora e idioma > Voz > Agregar voces > "Deutsch
  (Deutschland)", trae Hedda y Katja) y elegirla con `pit-wall voices --test`.
* Poner `engineer.language: de` (o `en`) en `config/local.yaml` y leer las frases de `docs/FRASES_VOZ.md`;
  decirme si el reconocimiento en alemán es suficiente y qué preguntas no entiende el enrutado local.

## 4. Seguridad y decisiones

* **Claves de reserva**: Gemini devuelve a menudo 503 ("high demand") y timeouts (2026-10-16) y el ingeniero cae al
  texto de respaldo. Una segunda clave (`GEMINI_API_KEY_2` o `GEMINI_API_KEY_LIST=a,b,c`) o un proveedor de
  reserva (`docs/PROVIDERS_PLAN.md`) lo reduce. Revisa los términos de Google antes de usar muchas cuentas
  gratuitas. El timeout bajó a 3,5 s (`llm.timeout_s`); si se pierden respuestas buenas, súbelo a 4,5.
* **Disco C: lleno** (quedan unos 800 MB): Windows y el juego escriben ahí. Los modelos de reconocimiento ya van
  a `E:\Documents\pit-wall-models` (`stt.model_dir`); libera algo más en C:.

## 5. Mensajes de pole y de resultado

Visto el 2026-10-17: el fin de qualy y el resultado de una carrera por vueltas salieron en vivo ("hemos cruzado la
meta en la posición 10"). Falta:

* una **qualy en la que salgas primero** para oír el mensaje de pole provisional y de pole;
* una **carrera por tiempo** (sin número de vueltas): solo existe **en línea** (en el servidor LFM, por ejemplo). El
  final ya se detecta con el dato del puente (tu coche terminó); falta verlo en una carrera real. Para probar en línea
  recuerda que `pit-wall record` y `voice` funcionan igual, y que el ingeniero dirá nombres de otros pilotos (ver
  `use_names` de las alertas).

## 5b. Memoria del ingeniero

* tras unas sesiones en vivo, pregunta "¿qué me dijiste de la curva 4 la última vez?" o "¿qué me dijiste de la
  gasolina?" y dime si la respuesta tiene sentido; el archivo es `.pit-wall-ai/memory.jsonl` en tu carpeta de
  usuario (borrarlo olvida todo; `engineer.memory.enabled: false` lo apaga).

## 5c. Planes aprobados el 2026-10-17: lo que hay que probar tú

Hecho y probado con pruebas y con tus grabaciones, falta verlo en el juego:

* **Debrief** (`docs/PHASE7_PLAN.md`): `pit-wall debrief sesiones\maserati_carrera.pwrec.gz` (añade `--summary`,
  `--speak`, `--save`). En vivo se dice solo al terminar una carrera o una clasificación (unos 12 s después del mensaje del
  resultado; no si "no molestes") y a petición: "dame el resumen de la sesión". Los informes quedan en
  `~/.pit-wall-ai/debriefs` (con una página `.html` con gráficos de las vueltas y de las curvas que cuestan tiempo; `--out`
  también la escribe). Si hay paradas en boxes, el informe dice cuánto cuesta cada una y la mediana queda guardada por
  circuito y coche. Para "contra la última vez" hace falta guardar sesiones del mismo coche y pista
  (`--save` o en vivo). Si no quieres que hable: `engineer.debrief.speak: false`.
* **Voces** (`docs/VOICES_PLAN.md`): oír el efecto de radio: `pit-wall say "Bloqueo delantero izquierdo" --radio light`
  (o `strong`); para dejarlo fijo `engineer.voice.radio_effect: light` en `config/local.yaml`. Piper (CPU, neuronal):
  `pip install piper-tts`, `pit-wall voices --piper-download es_ES-davefx-medium`, y lo que imprime en `config/local.yaml`.
  `pit-wall voices --prerender` deja las frases fijas en caché. Perfiles: `voice_profiles/<id>.yaml` y
  `engineer.voice.profile: <id>`; para clonar una voz con un servidor local (XTTS, GPT-SoVITS) usa el backend `http` con
  un clip **tuyo o con permiso**; el proyecto no trae ninguna voz de una persona real.
* **Plan de carrera** (`docs/PHASE8_PLAN.md` 8a): sin juego, `pit-wall plan --laps 20 --fuel-per-lap 2.0 --tank 25 --window 8-12
  --lap-time 80 --deg 0.05 --pit-loss 22` da el combustible, las paradas y las mejores vueltas para parar ("¿y si paro antes?").
  En el chat: pregunta "¿cuánta gasolina para 10 minutos?", "¿llego al final?",
  "plan de carrera" (te pide la longitud y si hay parada obligatoria; dile las reglas: "hay una parada obligatoria, ventana
  de la 8 a la 12"). La checklist: `pit-wall checklist add --kind stint --compound Medium --laps 5 --fuel 20`, `show`,
  `remove --id C1`, `undo`; o por voz "quita lo de mojado de la lista" (usa el modelo y siempre pasa por operaciones
  validadas, con deshacer). Se marca sola al completar las vueltas.
* **Privacidad de rivales** (principio 5 de la fase 8): los nombres de otros pilotos ya **no** se mandan al modelo (el
  ingeniero los dice en local); si quieres que el modelo los vea: `chat.rival_names_to_model: true`.
* Todavía **por decidir**: nada de las preguntas de los planes; los valores por defecto están en cada plan.

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

## 5l. Puente CSP: lo probado y lo que falta

Probado en vivo el 2026-10-17 (Maserati GT4, 16 coches): el desgaste real, las temperaturas de frenos por rueda,
los nombres de los rivales, la posición, los intervalos en segundos ("a 0,4 segundos", "se está acercando a 0,2") y
la comparación de la última vuelta; sin errores. Falta:

* **Encender las alertas** que quieras (`engineer.rules.rival_close`, `rival_pit`, `csp_flags`) en
  `config/local.yaml`; están apagadas hasta ver que los datos son buenos.
* El puente envía también grano, ampollas, puntos planos y kilómetros virtuales de los neumáticos (0.3): aún no los
  usa el ingeniero (el punto plano daría "bloqueaste el trasero izquierdo"; falta saber su escala).

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

* **Fase 8a, lo que espera datos**: la degradación por compuesto (corregida por combustible: el ranking de paradas ya existe pero
  toma la degradación que le des) y que el chat use la pérdida en boxes medida; para tener esa pérdida haz una parada
  en una práctica larga con `pit-wall record` (el debrief la mide solo).
* **Fase 8c** (sugerencias de reglaje y de estilo con registro de lo que se probó) y los motores de estrategia de
  4b (ventana de parada con tráfico, lluvia): siguen siendo planes.
* **Debrief**: el aviso en prácticas al cerrar el juego (no se puede detectar el final de una práctica).
* **Índice de desgaste por curva en vivo** con el porcentaje real del puente: el análisis ya existe y está
  validado (`tyre-corners`, también dentro del debrief como estimación); falta usarlo en la conversación en vivo.
* **Estimación del desgaste cuando el simulador no lo da** (iRacing, solo al parar): el cálculo ya existe
  (`analysis/wear_estimate.py`); falta una fuente de iRacing (fase multi-simulador).
* **Rivales sin CSP**: `telemetry/crewchief.py` lee la página `acpmf_crewchief` si activas la app **CrewChiefEx**.
* **Más traducciones al alemán**: hecho `docs/ENGINEER.de.md` y `tools/csp_bridge/README.de.md` (además de README y
  VOICE); si quieres otros, dime cuáles.
* Qué datos tienen las apps de tu instalación y cuáles usamos: `docs/AC_APPS_NOTES.md`. Los logs de Real Penalty
  tienen nombres de otros pilotos: se leen en local y no se suben.

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

* ~~Planes de debrief (fase 7), voces y plan de carrera (fase 8a)~~ (aprobados y construidos el 2026-10-17): ver 5c para lo que
  falta probar en el juego.
* ~~Clave de Gemini rotada~~ (2026-10-17) y ~~intervalos de rivales verificados contra CMRT~~ (coinciden).
* ~~Fin de semana completo con el Maserati GT4~~ (2026-10-17): práctica, qualy y carrera de 8 vueltas con la voz, el
  puente 0.3 y las grabaciones; verificados el desgaste real, las temperaturas de frenos, los rivales con intervalos,
  la bandera a cuadros, la de penalización (10 s, segundos por cumplir) y el daño; regla de daño encendida.
* ~~Líder, temperaturas, preguntas por el rival~~ (2026-10-17) y ~~números con coma dichos como hora~~.
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
