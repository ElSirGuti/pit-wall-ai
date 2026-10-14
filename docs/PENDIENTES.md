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

### 3. Probar el chat con el juego en vivo (opcional, 10 min)

```
.venv\Scripts\pit-wall chat
```

con una práctica abierta, y preguntar tras una vuelta: `¿cuánta gasolina queda?`, `¿dónde pierdo tiempo?`,
`¿cuál es la curvone?`, `¿qué fue eso?`. Interesa sobre todo `¿dónde pierdo tiempo?` en vivo (en las pruebas
solo se ha probado sobre grabaciones).

## Para la Fase 6 (voz), cuando la apruebes

Ver `docs/PHASE6_PLAN.md`, sección "Preguntas para el autor". Lo que necesitaré de ti:

* decidir qué botón será el push-to-talk y con qué micrófono hablas;
* grabar unas **30 a 40 frases habladas** (preguntas típicas, con números y nombres de curvas) para medir el
  reconocimiento de voz con tu voz y tu micrófono; idealmente algunas con el coche sonando de fondo.

## Ya cerrado

* ~~Pregunta "¿cuánto tiempo queda?" en carrera sin límite~~ (2026-10-15): ahora dice que la sesión no tiene
  límite de tiempo.
* ~~Forma de recortar una grabación grande a un tamaño publicable~~ (2026-10-15): `pit-wall trim`.
