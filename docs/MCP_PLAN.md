# Plan: the engineer as an MCP server (for people without an API key)

Status: **proposal, not approved, no code yet.** Requested by the author on 2026-10-14.

## 0. Why

The cloud chat of phase 5 needs an API key. Many people do not have one but do have an assistant that speaks
MCP (Claude Desktop and similar clients), often with its own voice mode. If pit-wall-ai is an **MCP server**,
that assistant becomes the conversation and pit-wall-ai stays what it is best at: reading the game, doing the
numbers and owning the rules. No key is configured in this project at all.

```
 Assetto Corsa -> shared memory -> pit-wall-ai (session, laps, braking, alerts, facts)
                                        |  MCP (stdio)
                                        v
                        the user's assistant (Claude Desktop...) -> its own voice mode -> the driver
```

## 1. What the server would expose

Read-only by default; every tool returns **facts and ready-made sentences**, never a free-form opinion.

| Tool | Returns | From |
|---|---|---|
| `session_summary` | track, session type, lap, position, time left | `build_facts` |
| `fuel` | litres, per lap, laps left, in the same words the local chat uses | `chat/local.py` |
| `tyres` | temperatures, pressures, wear per wheel, hottest | idem |
| `brakes` | temperatures (or "sensor not reliable") | idem |
| `laps` | best, last, delta, last N laps | idem |
| `corner(n or name)` | what is known of that corner: braking point, loss against the best lap | `chat/local._loss_of_corner`, `corner_text` |
| `where_am_i_losing_time` | worst corner of the last lap | analysis |
| `recent_alerts(n)` | what the engineer said and when | `Engineer.recent` |
| `recall(corner)` | what was told about that corner | `chat/recall.py` |
| `tyre_wear_by_corner` | the ranking, **only when validated** | `analysis/tyre_corners.py` |
| `facts` | the whole compact context (what the cloud chat receives) | `FactSheet.context()` |
| `set_quiet(on)` *(state-changing, opt in)* | the "do not bother me" mode | `Engineer.set_quiet` |
| `say(text)` *(state-changing, opt in)* | speaks through the local voice | `voice/` |

The answers carry the `say` field (the sentence to read) and the `facts` (the numbers). The server
instructions tell the client to read `say` verbatim and not to add numbers of its own.

## 2. Voice and proactive alerts

* **The conversation by voice** is the client's job (its microphone, its recognition, its voice). pit-wall-ai
  needs no microphone, no recognition and no model in this mode.
* **Alerts are push, MCP is pull.** The assistant cannot be interrupted by the server. So the critical rule
  alerts keep going out through pit-wall-ai's own local voice (SAPI), exactly as in phase 3, and the assistant
  can be asked "what did the engineer just say" (`recent_alerts`). Two voices in the same ears is the cost;
  the radio rules (first to start wins, critical always go out) already cover the local one.
* **The local voice is a setting** (requested by the author): `mcp.local_voice`.

  | Value | The local voice (SAPI) says |
  |---|---|
  | `on` | every rule alert, as without MCP |
  | `critical` (default) | only the critical alerts: the assistant can take the conversation, the car can still shout |
  | `off` | nothing: the alerts are only available to the assistant (`recent_alerts`), which reads them if asked |
  | `auto` | `on` while no MCP client is connected, `critical` while one is |

  The setting can also be changed by voice through the assistant (`set_quiet`, the existing "no molestes")
  and by the wheel's `mute_engineer` button, which overrides it until pressed again. With `off` the driver
  gets no warning of a puncture unless the assistant is asked: the README says so next to the setting.
* Latency of a turn is the client's (seconds): fine in the pits and on straights, not for a decision inside a
  corner. The rule alerts are unaffected.

## 3. Limits to state in the product

1. **The grounding check cannot see the client's words.** The numbers come from code, but the client may
   rephrase them wrongly. Mitigations: tools return finished sentences; `check_claims(text)` runs the same
   grounding check on any text the client is about to say and returns the violations; the instructions say to
   quote. It is weaker than the cloud chat, where the check sits between the model and the driver. Say so.
2. **Privacy:** what a tool returns goes to the user's assistant provider. Same consent as `llm.allow_cloud`:
   the server refuses to start unless the user confirms it (`mcp.consent: true`).
3. **No telemetry stream**: only snapshots at the moment of the call.
4. Windows, same machine as the game; stdio transport only at first (the way Claude Desktop launches
   servers). A network transport is a later, separate decision with its own security review.

## 4. Steps (each waits for approval)

| Step | Content | Done when |
|---|---|---|
| 1 | `pit-wall mcp` stdio server with the read-only tools, over `ChatRuntime`, official `mcp` SDK as an optional extra | a client lists the tools and gets facts on a replayed recording |
| 2 | `check_claims`, server instructions, `mcp.consent`, the opt-in state-changing tools | tests with a fake client; a bad number is flagged |
| 3 | live: poller + local alerts through the local voice while the assistant converses | end to end with Claude Desktop on the author's PC |
| 4 | docs: `claude_desktop_config.json` snippet, other clients, limits | README ES/EN |

## 5. Risks

* The `mcp` SDK moves quickly: pin a version and keep the server thin (the tools are plain functions that the
  tests call directly).
* Tool answers that are too long make the client's voice read a lot: every tool has a short mode by default.
* Users may expect the proactive behaviour of the full engineer from the assistant: the README must say that
  the alerts are the local voice's and the assistant is the pit wall's chat.
* Two assistants talking at once (the local voice and the client's): solved by `mcp.local_voice`, with
  `critical` as the default so a safety alert is never lost silently.

## 6. What the author can contribute

The MCP servers the author has built: their conventions (tool naming, short vs long answers, error shapes,
consent and logging) would be reused so this one looks like the rest. Share the documentation and I align the
plan with it before step 1.
