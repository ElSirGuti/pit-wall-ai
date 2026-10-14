# Plan: the engineer as an MCP server, with general MCP conventions

Status: **proposal, deferred by the author (2026-10-14): nice to have, not urgent. No code.** Same proposal as `MCP_PLAN.md`, rewritten to follow the
conventions that usually make an MCP server dependable for an assistant (section 7). No private or
organisation-specific detail is used here.

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

  **Who says "lap 2, three tenths up" at the end of a lap?** The local voice, not the assistant: the lap
  summary is a rule alert of medium priority, so with `critical` it would be silenced too. That is why the
  setting has a companion list, `mcp.local_voice_always: [lap_summary]` (default): the rules named there
  are spoken in every mode except `off`, on top of the critical ones. The driver chooses which routine
  alerts stay (lap summary, braking hint, fuel warning...) and which are left to the assistant.

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

## 6. Conventions this server will follow

These are general practices for MCP servers, written without reference to any particular deployment.

### 6.1 Tools

1. **The description is the contract.** A tool's docstring tells the assistant what it returns, in which
   units, and *when* to call it ("call this when the driver asks about fuel or laps left"). It is written for
   the model, not for the maintainer.
2. **Small, single-purpose, read-only tools** that map to one question. State-changing tools are a separate,
   clearly named group, switched off unless the user opts in.
3. **Typed returns**: an object (`dict`) for one thing, a list for many, never a bare string. Every answer
   has the same envelope: `{ "say": <sentence to read>, "facts": {...}, "age_s": <seconds since the data>,
   "available": true|false, "why": <reason when not> }`.
4. **Short by default**, with an explicit `detail=true` for the long form: a voice client reads everything it
   receives.
5. **Names are stable**: renaming a tool breaks every client configuration that mentions it. New behaviour
   is a new tool or an optional parameter.
6. **Deterministic**: the same state returns the same answer; no tool asks a model for text.

### 6.2 Errors and absence of data

* *No data* is an answer, not an error: `available: false` plus a reason in plain words ("the game is not
  running", "no valid reference lap yet"). The assistant must be able to say that without guessing.
* Real errors use a short, structured message (kind + what to do), never a stack trace.
* **Fail closed**: without the user's consent (section 3) every tool refuses, with the reason.

### 6.3 Safety and privacy

* Local transport only (stdio launched by the client); nothing listens on the network.
* The server never returns secrets (keys, tokens, paths of the user's profile); logs never contain them.
* Consent is explicit and recorded in the configuration; changing it needs a restart.
* Every state-changing call is logged with its time, so "why did the engineer go quiet" is answerable.

### 6.4 Composition

* **Aggregators over duplication**: a tool that combines several areas (a "where am I losing time" that
  uses laps, braking and the corner map) reads the existing analysis; it does not recompute.
* One server per audience and per permission level. Here a single server is enough; if a state-changing
  group is ever shared with other people's assistants it becomes a second server.

### 6.5 Testing

1. The tools are plain functions: the unit tests call them directly on a recorded session, no client needed.
2. A thin protocol test lists the tools and calls one through an in-memory client.
3. Try first in the simplest transport (stdio, no authentication layer) before any remote setup.
4. A golden test per tool: the answer on a fixed recording is stored and reviewed when it changes.

### 6.6 Client configuration

The client is told how to start the server (a command and its arguments, one entry per server). The README
gives a ready-to-paste snippet per client and a one-line check ("ask: what tools does the engineer have?").

## 7. What would change if the server were remote

Not planned. For completeness: an HTTP transport needs an authentication flow, certificate handling,
per-user permission flags and a reverse proxy, each with its own failure modes (discovery documents, host
header validation, token errors in the format the client expects). That is a separate proposal with its own
security review, and the local single-user case should not pay for it.
