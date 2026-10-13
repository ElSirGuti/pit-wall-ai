# Input bindings (plan for phase 6, used by phase 8)

Status: **plan only.** Decision of 2026-10-13: any action can be bound to **any button of any input device on
the PC**: keyboard, mouse, gamepad, steering wheel, button box. Nothing here has been tried on hardware yet; the
"to verify" column says what must be checked on the author's devices before it is called working.

*Resumen en español al final.*

## 1. Actions (what can be bound)

| Action | Behaviour | Used by |
|---|---|---|
| `push_to_talk` | hold: the microphone is open while it is down | phase 6 |
| `feedback_better` / `feedback_same` / `feedback_worse` | tap: answers "how does it feel?" after a test stint | phase 8c |
| `repeat_last` | tap: say the last message again | phase 3/6 |
| `mute_engineer` | toggle: silence the non-critical voice | phase 3/6 |
| `checklist_next` / `checklist_skip` | tap: advance or skip the active checklist item | phase 8a |
| `cancel` | tap: stop the current speech / discard the last recording | phase 6 |

An action may have several bindings (a wheel button and a key), and a binding may be a **chord** (a modifier
plus a button) to avoid collisions with the game's own controls.

## 2. What counts as an input

| Kind | Examples | Identified by | Notes |
|---|---|---|---|
| keyboard key | `F9`, `Ctrl+Space` | virtual-key code | global (works while the game has focus) |
| mouse button | side buttons 4 and 5, wheel click | button id | global; plain left/right click is refused as a binding |
| gamepad / wheel / button box button | G29 paddle, button 17 of a box | device id + button index | DirectInput-style joystick devices |
| hat / D-pad direction | up, down, left, right | device id + hat + direction | treated as four buttons |
| axis past a threshold | a pedal or a trigger | device id + axis + threshold + direction | optional, for pedals/triggers |

## 3. How a binding is created: "press it now"

```
pit-wall bind push_to_talk
> Press the control you want for push_to_talk (Esc to cancel)...
> Detected: G29 Driving Force Racing Wheel, button 7  (device 046d:c24f)
> Saved to config/local.yaml
```

* The tool listens to **every** device at once and takes the first press, so the user never has to say what kind
  of device it is.
* It refuses an input that is already bound to another action, and warns when it is a plain click or a key
  typed into text (Enter, Space without modifier is allowed but flagged: it is also used by the game).
* `pit-wall bindings` lists them, `pit-wall bind --clear push_to_talk` removes one, and `pit-wall input-test`
  prints every press and release with the device name so a device can be checked without binding anything.

## 4. Configuration

```yaml
input:
  poll_hz: 200            # joystick polling rate; keyboard and mouse are event driven
  bindings:
    push_to_talk:
      - {kind: joystick, device: "046d:c24f", name: "G29 Driving Force Racing Wheel", button: 7}
      - {kind: key, key: "F9"}
    feedback_better: [{kind: joystick, device: "046d:c24f", button: 12}]
    mute_engineer:   [{kind: mouse, button: "x2"}]
```

Devices are matched by vendor and product id (`046d:c24f`) first, by name second, and **not** by the
instance GUID, which changes with the USB port. If two identical devices are connected, the instance order is
stored as a tie-breaker and a warning is logged when it cannot be told.

## 5. Behaviour that must hold

* **Never blocks**: input runs in its own thread and publishes events; the telemetry loop does not wait for it.
* **Hot-plug**: a device that is unplugged and plugged back in is re-acquired without restarting.
* **Does not take the device from the game**: joysticks are read in shared (non-exclusive) mode; the game and
  the tool see the same presses. The user must pick a button the game does not use for something important;
  the tool cannot know the game's bindings and says so in `bind`.
* **Latency**: press to "microphone open" under 30 ms at 200 Hz polling (measured and reported like the
  voice latency, not assumed).
* **Failure is visible**: if an input backend cannot start (permissions, missing library) the engineer says so
  once and falls back to the bindings that do work.

## 6. Implementation options and the decision to make when the phase starts

| Option | For | Against |
|---|---|---|
| `pynput` for keyboard and mouse + `pygame`/SDL2 joystick API for devices | one library covers most devices, up to 128 buttons, hat and axes, works in the background with a hint | pygame is heavy; SDL needs a hidden window/video-less init |
| WinMM (`joyGetPosEx` through `ctypes`) | no dependency | **32 buttons and 6 axes per device**: a large button box does not fit; legacy API |
| DirectInput through `ctypes`/`comtypes` | complete | complicated and fragile |
| `keyboard`/`mouse` libraries | tiny | global hooks only; no joysticks |

Recommended starting point: `pynput` + SDL2 joystick (`pygame-ce` or `pysdl2` with only the joystick
subsystem), behind an interface so the backend can be swapped. To be confirmed by a spike on the author's
real devices before committing to it.

## 7. To verify on real hardware (nothing is marked working until it is)

Author's devices (2026-10-13): Logitech G29 (G HUB installed), keyboard, mouse; **no button box**, no
duplicate devices.

1. The G29 buttons and paddles are seen while the game has the focus, **with G HUB running** (it can remap or
   swallow buttons: check the profile it applies to the game and to the tool).
2. The keyboard and the mouse side buttons work as global bindings with the game focused.
3. Sharing the wheel with the game in non-exclusive mode does not disturb force feedback or the game inputs.
4. Hot-plug: unplug and replug the wheel during a session.
5. Button boxes and several identical devices are supported by the design but **untested** (the author has none).

## 8. Tests that do not need hardware

An in-memory fake backend produces press/release events, so the action layer (hold, tap, toggle, chords,
conflict detection, matching by vendor/product id, hot-plug) is tested like any other module.

---

## Resumen en español

Cualquier acción (`push_to_talk`, feedback "mejor/igual/peor", repetir el último mensaje, silenciar, avanzar el
checklist…) se puede asignar a **cualquier botón de cualquier dispositivo**: teclado, ratón (incluidos los
laterales), mando, volante o botonera. Se asigna con `pit-wall bind <acción>` y **pulsando el control**; la
herramienta escucha todos los dispositivos a la vez y toma la primera pulsación, sin que tengas que decir de qué
tipo es. Soporta acordes (modificador + botón), varios botones por acción y se identifica por vendor/producto, no
por el GUID, que cambia con el puerto USB. Funciona en segundo plano, comparte el volante con el juego y no
bloquea la telemetría. Nada está probado todavía en hardware: la lista de la sección 7 es lo que hay que
comprobar con tu G29, tu teclado, tu ratón y tu botonera antes de darlo por bueno.
