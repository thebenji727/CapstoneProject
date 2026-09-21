# Loader schemas and failure modes

Date: 2026-09-21
Project: A Data-Driven Teambuilder and Set Recommendation System for Pokémon Champions VGC

The UI must stay up if Pikalytics or pokedata change a field name. Live JSON is parsed into a normalized `Team` snapshot (`species`, `item`, `ability`, `moves`, `weight`). Snapshots live in `data/cache/`. A schema break on the next boot loads the last good snapshot instead of crashing `/api/recommend`.

## Default `load_teambuilder_data()` path

| Source | Default | Why |
| --- | --- | --- |
| Pikalytics team-usage `gen9championsvgc2026regmc` | ON | Current format pairing weights |
| Pikalytics sample pastes (M-C + championstournaments) | ON | Items / moves when official pages are thin |
| Limitless cups whose title says Reg M-C / M-C | ON | Fresh M-C pastes. Filter is no longer `champion\|vgc\|M-B` |
| Worlds 2026 Masters (`0000191`) | ON, weight × 0.35 | Full open sheets for set fill. Meta is M-B, so it cannot outvote M-C usage |
| Official 2026 ICs / Regionals / Specials | OFF | Still the Play! M-B calendar. Opt in with `include_official_season=True` |

## Three loaders

1. `load_team_usage` — Pikalytics groups of 6 names + item + uses. Snapshot: `data/cache/pika_usage_regmc.json`.
2. `load_worlds` / `load_official_event` — pokedata open team sheets. Snapshot: `data/cache/worlds_2026_masters.json`.
3. `load_recent_limitless` — finished cups, 16+ players, Reg M-C in the title. Snapshot: `data/cache/limitless_regmc.json`.

Schemas: `docs/schemas/`.

## Failure modes

| Failure | What the user sees | What the code does |
| --- | --- | --- |
| pokedata timeout / 5xx | UI still loads | Worlds snapshot if present; else skip Worlds |
| pokedata adds a new decklist field and drops `name` | UI still loads | Parse yields 0 teams → snapshot. Old lists keep items/moves |
| Pikalytics team-usage shape changes | Partners look thinner | Usage snapshot; Limitless + Worlds still vote |
| Limitless list endpoint changes | Fewer current-cup pastes | Limitless snapshot; M-C usage still drives partners |
| Empty Reg M-C cup page this week | Status line still ready | Filter skips non-M-C titles; cache from last successful pull |
| Official championsbattledata missing a new mon | Set card may infer alignment | `load_details` already merges M-C → M-B → official → infer |
| Whole `_load()` throws | Status error string, Recommend disabled | Last resort. Individual loaders no longer raise into this path |

## What this is not

This week does not rebuild the recommendation engine. Off-meta fallbacks and set-slice cleanup are the Sep 22 engine pass.

## How to confirm

1. Restart `py -3 webapp\server.py`.
2. First successful boot writes files under `data/cache/`.
3. Unplug the network and restart. Status should still become ready if those files exist.
