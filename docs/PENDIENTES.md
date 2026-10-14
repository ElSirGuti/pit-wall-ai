# Pendientes del autor

Lista viva de lo que **tiene que hacer una persona** (no el código). Se actualiza cuando algo se cierra.
Última actualización: 2026-10-15.

## Cuando vuelvas a casa (con el juego abierto)

### 1. Grabar una segunda sesión para la evaluación del chat  ·  ~20 min

La evaluación del chat (`pit-wall eval`) solo se ha probado con **una** ventana de carrera (Magione, MX-5, 6
minutos). Para que el 34/34 signifique algo más hace falta otro coche u otra pista.

Qué grabar:

* **Otro coche y/o otra pista**. Lo mejor: un coche donde la **temperatura de frenos sí se mueva** (los GT3/GT4
  suelen tenerla; el Supra GT4 no) y una pista con nombres de curvas en la tabla (Spa, Imola, Mugello) o
  Magione con otro coche.
* **Práctica de 6 a 8 vueltas**; en una o dos, frena claramente antes o después en una curva (para que haya
  avisos de frenada), y si quieres provoca un trompo o una salida de pista.

Comandos (dos terminales, como la otra vez):

```
cd E:\Documents\pit-wall-ai
.venv\Scripts\pit-wall record --out sesion2.pwrec.gz
```

y, si quieres oír los avisos mientras grabas, en la otra terminal:

```
.venv\Scripts\pit-wall run --voice --plain --log-level INFO
```

Al terminar (`Ctrl+C`), recorta una ventana pequeña para el repositorio (menos de 1 MB):

```
.venv\Scripts\pit-wall trim --file sesion2.pwrec.gz --from 0 --to 420 --rate 10 --out tests\fixtures\live_<pista>_<coche>_window.pwrec.gz
```

(ajusta `--from`/`--to` en segundos para quedarte con las vueltas buenas; `--rate 10` basta).

Luego, **con la clave nueva** en `.env` (ver el punto 2) y `config\local.yaml` con `provider: gemini` y
`allow_cloud: true`:

```
.venv\Scripts\pit-wall eval --file tests\fixtures\live_<pista>_<coche>_window.pwrec.gz --session practice --out eval_report_2.md
```

Y me pasas el `.pwrec.gz` recortado y el resultado (o `eval_report_2.md`). Yo lo añado como fixture y amplío el
conjunto. Los casos están escritos sobre **hechos de la sesión**, no sobre números fijos, así que valen para
cualquier grabación.

### 2. Rotar la clave de Gemini  ·  2 min

La clave que me pasaste quedó escrita en la conversación. Aunque ya la quité del disco (`.env` está vacío):

1. Entra a Google AI Studio → API keys y **borra** la que usé.
2. Crea una nueva **solo** para el proyecto (proyecto de AI Studio aparte, sin facturación).
3. Ponla en `E:\Documents\pit-wall-ai\.env` como `GEMINI_API_KEY=...` cuando vayas a repetir pruebas.

### 3. Probar el botón de push-to-talk con tu volante  ·  5 min

Es lo único del paso 1 de la Fase 6 que no he podido comprobar sin ti (necesita que pulses un botón). Con el
G HUB abierto, como cuando juegas:

```
.venv\Scripts\pit-wall input-test --seconds 30
```

pulsa y suelta botones y paletas del G29, una tecla (por ejemplo F9) y un botón lateral del ratón. Debe salir
una línea `press` y otra `release` por cada pulsación, con el nombre del dispositivo. Si algún botón del volante
no sale, dímelo (puede ser el G HUB). Cuando salgan todos, asigna el tuyo:

```
.venv\Scripts\pit-wall bind push_to_talk
.venv\Scripts\pit-wall bindings
```

### 4. Probar el chat con el juego en vivo (opcional, 10 min)

```
.venv\Scripts\pit-wall chat
```

con una práctica abierta, y preguntar tras una vuelta: `¿cuánta gasolina queda?`, `¿dónde pierdo tiempo?`,
`¿cuál es la curvone?`, `¿qué fue eso?`. Interesa sobre todo `¿dónde pierdo tiempo?` en vivo (en las pruebas
solo se ha probado sobre grabaciones).

### 5. Capturas para los eventos (banderas, daños, safety car)  ·  cuando puedas

Ninguna regla de banderas ni de daños está activada porque **nunca se ha capturado un evento real**. Para cada
una, una grabación corta con `pit-wall record` (basta un minuto alrededor del evento):

* **bandera amarilla** y **bandera azul** (contra coches de la IA: ponte detrás de uno lento o deja que te
  doblen);
* **bandera negra / penalización** si puedes provocarla;
* **un contacto que cause daño** (choca contra un muro a poca velocidad);
* **safety car o penalización con Real Penalty activado**, y de esa sesión el archivo de log más reciente de
  `...\assettocorsa\apps\python\RealPenalty\logs\` (se llama `session_<fecha>.log`).

Con eso se activan las reglas con datos reales, y se decide si el safety car se lee del log de Real Penalty o
con una app puente. Detalle en `docs/ENGINEER_EXPERIENCE_PLAN.md`, secciones 3 y 8.

## Para la Fase 6 (voz)

Ver `docs/PHASE6_PLAN.md`, sección "Preguntas para el autor". Lo que necesitaré de ti:

* ~~decidir qué botón será el push-to-talk y con qué micrófono hablas~~ (respondido: configurable; el botón lo asignas tú con `pit-wall bind`);
* decidir cómo quieres que se comporte un aviso medio mientras te contesta el ingeniero (ver el ejemplo en el plan);
* ~~cómo se comporta un aviso medio mientras contesta el ingeniero~~ (decidido: gana el que empieza primero; los críticos siempre salen);
* en casa, con el paquete de voz instalado (`pip install -e ".[voice]"`): ejecutar `pit-wall devices`, `pit-wall mic-test --device seiren` (o el casco) y `pit-wall voices --test`, y anotar qué micrófono, qué salida y qué voz prefieres para ponerlos en `config/local.yaml`;
* si quieres otra voz: bájala en Configuración de Windows > Hora e idioma > Voz > Agregar voces, y vuelve a ejecutar `pit-wall voices`;
* medir el efecto de `stt.device: cuda` vs `cpu` en los fps del juego: `pit-wall stt-test --device both` con el juego abierto y comparar fps (instalar `pip install -e ".[stt,stt-cuda]"`);
* grabar las **40 frases habladas** de `docs/FRASES_VOZ.md` (tanda en silencio y tanda con el coche sonando)
  para medir el reconocimiento de voz con tu voz y tu micrófono;
* decidir sobre "psi" mal reconocido: tabla de correcciones o modelo `medium` (`docs/PHASE6_PLAN.md` 7.1);
* idea nueva (2026-10-14): **desgaste de neumáticos por curva** con consejo para reducirlo, ver
  `docs/PHASE8_PLAN.md` 3.4; espera tu aprobación del plan y una grabación de al menos 8 vueltas.

## Ya cerrado

* ~~Pregunta "¿cuánto tiempo queda?" en carrera sin límite~~ (2026-10-15): ahora dice que la sesión no tiene
  límite de tiempo.
* ~~Forma de recortar una grabación grande a un tamaño publicable~~ (2026-10-15): `pit-wall trim`.
