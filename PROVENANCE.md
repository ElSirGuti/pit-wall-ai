# Provenance / Procedencia

Where each piece of this project comes from, so nothing is redistributed by accident.
De dónde sale cada pieza, para no redistribuir nada por error.

| Piece | Origin | Notes |
|---|---|---|
| `telemetry/shm_structs.py` | **Written for this project** (own names, grouping and documentation) | The byte layout (field order and types) is a fact of the game's binary interface. It was read from the game's Python helper in a stock install (`apps/python/acti/sim_info_acti.py`) as a reference and checked live: the `static` page decodes correctly end to end with AC 1.16.4 / shared-memory 1.7. No file from the Kunos install is copied or shipped |
| `telemetry/ac_shared_memory.py` | Written for this project | Read-only `OpenFileMappingW` access, torn-read retry, stale-page detection |
| `telemetry/recording.py` (`.ld` reader) | Reimplemented from the author's own `motorsport-analytics-pipeline` (`src/io/ld_loader.py`, commit `f47d319`, MIT, same author), simplified | Follows the publicly described MoTeC `.ld` structure |
| `telemetry/recording.py` (CSV reader) | Written for this project | MoTeC CSV export layout as seen in ACTI exports |
| `telemetry/replay.py` channel mapping | Written for this project | ACTI channel names as exported by the ACTI app; approximate, see `docs/AC_SHARED_MEMORY.md` |
| `analysis/track_geometry.py` (corner segmentation) | Reimplemented from the author's own `motorsport-analytics-pipeline` (`src/analytics/ac_track_geometry.py`, MIT, same author) on numpy only | Parameters and method unchanged; no `fast_lane.ai` data is shipped |
| `data/circuits.json` | Subset (names and apex fractions) of the author's own `src/data/circuits.json` in the same project | Corner names are public facts about circuits; apex fractions were fitted by the author on their own recordings |
| Everything else (poller, config, LLM layer, dashboard, CLI) | Written for this project | |

## Not included on purpose / No incluido a propósito

* **Track geometry.** `motorsport-analytics-pipeline` ships curvature profiles derived from the tracks'
  `fast_lane.ai`. Those files come from game content (Kunos and third-party track authors) and their
  redistribution terms are unclear, so **this repository ships none of them**. Phase 4 generates them
  locally from the user's own installation into an ignored folder (`data/`); the tool reads
  `<AC install>/content/tracks/<track>/ai/fast_lane.ai` on the user's machine and nothing derived is committed.
* **Recordings** (`*.ld`, `*.csv`, `*.ibt`) and secrets (`.env`, API keys) are git-ignored. Tests use synthetic data;
  a real log can be pointed to with `PIT_WALL_SAMPLE_LD` and is never copied into the repo.
* No Kunos binaries, textures, sounds or data files.

## Corner names / Nombres de curvas

Corner names and apex positions of known circuits (used from phase 4) will be the author's own table
(`circuits.json` in the sibling project), not data taken from the game.
