# Allocator Frontend

This directory contains the small React-based admin frontend for the allocator backend.

It is intentionally narrow:

- React
- static build served by nginx
- `/api` proxied to the in-cluster allocator backend
- no auth
- PostgreSQL-backed tournament planning data through the backend

## UI Scope

- backend health status
- Tournament Management for persisted tournaments, teams, rounds, and tournament matches
- persisted server assignment controls for tournament matches
- Match Room creation
- pre-allocation map/mode selection
- Match Room allocation and release
- live player/map/score status for allocated Match Rooms
- whitelisted admin controls for allocated Match Rooms: broadcast message and change map
- allocated-server infrastructure controls: command panel and terminate
- Fleet summary
- current `GameServer` list
- direct/manual allocation as a collapsed advanced/debug action

Match Rooms are the primary admin-facing objects. Allocated `GameServer` instances are the infrastructure backing those rooms. `Ready` servers are standby/internal capacity and are not presented as join targets.

Map and game mode are selected before allocation. The backend still uses a warm Agones Fleet, so it allocates an already-running server, configures it through whitelisted RCON, verifies with `getstatus`, and only then exposes a join endpoint. Max-player control remains deferred.

The mode selector drives the map selector from `GET /game-config/options`. Only verified combinations are shown in normal create/allocation flows: `dm` on `xoylent`, `stormkeep`, or `solarium`, and `tdm` on `stormkeep`. `ctf` and `duel` remain hidden from normal selection until they are verified in this cluster flow.

Post-allocation admin controls are intentionally narrow: broadcast a message with RCON `say` and use an allowlisted map override with RCON `changelevel`. The UI does not expose raw RCON command input or the RCON password.

Manual Direct Allocation is hidden under Advanced / Debug and should only be used for lower-level allocator testing.

Allocated Servers are infrastructure allocations. The table can terminate an `Allocated` GameServer directly; Fleet/FleetAutoscaler should replenish capacity afterward. The Commands panel routes safe actions through a linked Match Room when available and keeps direct/manual allocations disabled for RCON actions.

The Tournament Management section is the first UI over PostgreSQL-backed tournament APIs. It lets operators create/select tournaments, add teams, add rounds, and create simple tournament match records. Tournament matches can now allocate and release a persisted server assignment that links the match to one allocated Agones `GameServer`.

Current tournament limitations:

- no bracket visualization
- no automatic winner advancement
- no result recording UI yet
- match names are not persisted by the backend schema yet; the UI displays match IDs
- tournament match RCON controls are still handled through the lower-level Match Room workflow for now

## Backend Endpoints Used

- `GET /healthz`
- `GET /game-config/options`
- `GET /fleet-status`
- `GET /gameservers`
- `GET /tournaments`
- `POST /tournaments`
- `GET /tournaments/<tournament_id>/teams`
- `POST /tournaments/<tournament_id>/teams`
- `GET /tournaments/<tournament_id>/rounds`
- `POST /tournaments/<tournament_id>/rounds`
- `GET /tournaments/<tournament_id>/matches`
- `POST /tournaments/<tournament_id>/matches`
- `GET /tournaments/<tournament_id>/matches/<match_id>/server-assignments`
- `POST /tournaments/<tournament_id>/matches/<match_id>/allocate-server`
- `POST /tournaments/<tournament_id>/matches/<match_id>/release-server`
- `GET /matches`
- `POST /matches`
- `POST /matches/<match_id>/allocate`
- `POST /matches/<match_id>/release`
- `POST /matches/<match_id>/admin/broadcast`
- `POST /matches/<match_id>/admin/change-map`
- `POST /allocated-servers/<gameserver_name>/terminate`
- `POST /allocate`
