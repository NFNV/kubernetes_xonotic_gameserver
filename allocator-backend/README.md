# Allocator Backend

This directory contains the first in-cluster allocator backend for the project.

It is intentionally small:

- Python
- one HTTP process
- PostgreSQL-backed tournament CRUD
- basic password-protected admin sessions for mutating/operator actions
- simple JSON API that can be consumed by the operator frontend

The service runs inside Kubernetes and uses the Kubernetes API directly to create/read `GameServerAllocation` resources and delete allocated `GameServer` resources in `xonotic-agones`.

That is the right choice for this phase because the backend already runs in-cluster and only needs the simplest possible path to allocate from the existing Agones Fleet.

## Tournament Persistence

This phase adds the first PostgreSQL-backed tournament foundation.

Implemented now:

- create/list/get tournaments
- create/list teams for a tournament
- create/list rounds for a tournament
- create/list tournament matches
- generate 2-, 4-, and 8-team single-elimination brackets from seeded teams, with safe regeneration before server assignments or results exist
- record tournament match results and mark matches finished
- advance generated bracket winners into their next match slot after result recording
- allocate/release one persisted server assignment for a tournament match
- minimal startup migrations for `tournaments`, `teams`, `players`, `rounds`, `matches`, and `match_server_assignments`

Still deferred:

- double elimination, Swiss, and round robin formats
- persisted live telemetry history
- stronger auth and production database hardening

The existing in-memory Match Room, allocation, `getstatus`, RCON, and release flow remains process-local and unchanged in this phase. Tournament match server assignments are now persisted separately in PostgreSQL.

## Match Rooms

Match Rooms are the first admin-facing layer above raw Agones allocation.

A Match Room represents an operator-created match/session and can have one allocated Xonotic `GameServer` assigned to it. The allocated `GameServer` is the infrastructure backing the room; standby `Ready` servers remain internal capacity.

Match Rooms are stored only in backend process memory for now. They disappear when the backend Pod restarts. That is intentional for this MVP because there is no persisted Match Room model or player account model yet.

For allocated rooms, the backend also sends a read-only UDP `getstatus` query to the assigned Xonotic server. That response is cached briefly and used to fill live player count, map, game mode, player names, scores, ping, and team score data when available. This does not use RCON.

Map and game mode are selected on the Match Room before allocation. Because the Fleet is warm, allocation does not create a fresh preconfigured server. The backend allocates a Ready server, applies the requested map/mode through whitelisted RCON, verifies the result with `getstatus`, and only then marks the room joinable.

Max-player control is still deferred. Live status remains the source of truth for the running server.

Releasing a Match Room deletes the allocated Agones `GameServer` resource, removes the user-facing endpoint from the room, and lets the Fleet/FleetAutoscaler create replacement standby capacity.

## Map/Mode Compatibility

Map/mode selection is intentionally conservative. The backend owns a central compatibility matrix and exposes it through `GET /game-config/options` for the frontend.

Selectable combinations are only combinations verified in this project by applying RCON config and confirming the result with `getstatus`:

- `dm`: `xoylent`, `stormkeep`, `solarium`
- `tdm`: `stormkeep`
- `ctf`: `runningmanctf`
- `duel`: `xoylent`
- `ca`: `stormkeep`, `xoylent`

Other `ctf`, `duel`, and `ca` maps plus `dom` and `kh` remain deferred/experimental and are not selectable for normal Match Room or tournament match allocation yet. Invalid combinations are rejected before a match is created or before any Agones allocation is attempted.

Use `docs/tournament-map-mode-verification.md` and `scripts/verify-tournament-map-mode.sh` to prove a candidate pair through the tournament allocation flow before promotion. Experimental probes require the backend to be temporarily deployed with `XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG=1`; default runtime validation still accepts only verified combinations.

## API

- `GET /healthz`
- `GET /admin/session`
- `POST /admin/login`
- `POST /admin/logout`
- `GET /game-config/options`
- `GET /fleet-status`
- `GET /gameservers`
- `POST /tournaments`
- `GET /tournaments`
- `GET /tournaments/<tournament_id>`
- `POST /tournaments/<tournament_id>/teams`
- `GET /tournaments/<tournament_id>/teams`
- `POST /tournaments/<tournament_id>/rounds`
- `GET /tournaments/<tournament_id>/rounds`
- `POST /tournaments/<tournament_id>/bracket/generate`
- `POST /tournaments/<tournament_id>/matches`
- `GET /tournaments/<tournament_id>/matches`
- `POST /tournaments/<tournament_id>/matches/<match_id>/result`
- `GET /tournaments/<tournament_id>/matches/<match_id>/server-assignments`
- `POST /tournaments/<tournament_id>/matches/<match_id>/allocate-server`
- `POST /tournaments/<tournament_id>/matches/<match_id>/release-server`
- `POST /tournaments/<tournament_id>/matches/<match_id>/admin/broadcast`
- `POST /tournaments/<tournament_id>/matches/<match_id>/admin/change-map`
- `POST /matches`
- `GET /matches`
- `GET /matches/<match_id>`
- `POST /matches/<match_id>/allocate`
- `POST /matches/<match_id>/release`
- `POST /matches/<match_id>/rcon-smoke-test`
- `POST /matches/<match_id>/admin/broadcast`
- `POST /matches/<match_id>/admin/change-map`
- `POST /allocated-servers/<gameserver_name>/terminate`
- `POST /allocate`

`POST /allocate` remains available as a direct/manual allocation test endpoint. The operator UI should prefer Match Rooms; direct allocation is an advanced/debug path.

Mutating endpoints require an admin session cookie:

```bash
ADMIN_COOKIE="$(mktemp)"
curl -fsS -c "${ADMIN_COOKIE}" -X POST http://127.0.0.1:18080/admin/login \
  -H "content-type: application/json" \
  -d '{"username":"admin","password":"admin"}'
```

Create tournament records:

```bash
TOURNAMENT_ID="$(curl -fsS -b "${ADMIN_COOKIE}" -X POST http://127.0.0.1:18080/tournaments \
  -H "content-type: application/json" \
  -d '{"name":"Spring Arena Cup","description":"Manual MVP tournament"}' | jq -r .id)"

curl -fsS http://127.0.0.1:18080/tournaments | jq
curl -fsS "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}" | jq
```

Create teams, a round, and a tournament match:

```bash
TEAM_A_ID="$(curl -fsS -b "${ADMIN_COOKIE}" -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/teams" \
  -H "content-type: application/json" \
  -d '{"name":"Blue Rockets","tag":"BLUE","seed":1}' | jq -r .id)"

TEAM_B_ID="$(curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/teams" \
  -H "content-type: application/json" \
  -d '{"name":"Orange Railers","tag":"ORNG","seed":2}' | jq -r .id)"

ROUND_ID="$(curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/rounds" \
  -H "content-type: application/json" \
  -d '{"name":"Round 1","round_order":1}' | jq -r .id)"

MATCH_ID="$(curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches" \
  -H "content-type: application/json" \
  -d "{\"round_id\":\"${ROUND_ID}\",\"team_a_id\":\"${TEAM_A_ID}\",\"team_b_id\":\"${TEAM_B_ID}\",\"requested_map\":\"stormkeep\",\"requested_game_mode\":\"dm\"}" | jq -r .id)"
```

Check selectable game config and validation behavior:

```bash
curl -fsS http://127.0.0.1:18080/game-config/options | jq

curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches" \
  -H "content-type: application/json" \
  -d "{\"round_id\":\"${ROUND_ID}\",\"team_a_id\":\"${TEAM_A_ID}\",\"team_b_id\":\"${TEAM_B_ID}\",\"requested_map\":\"xoylent\",\"requested_game_mode\":\"dm\"}" | jq

curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches" \
  -H "content-type: application/json" \
  -d "{\"round_id\":\"${ROUND_ID}\",\"team_a_id\":\"${TEAM_A_ID}\",\"team_b_id\":\"${TEAM_B_ID}\",\"requested_map\":\"stormkeep\",\"requested_game_mode\":\"tdm\"}" | jq

curl -i -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches" \
  -H "content-type: application/json" \
  -d "{\"round_id\":\"${ROUND_ID}\",\"team_a_id\":\"${TEAM_A_ID}\",\"team_b_id\":\"${TEAM_B_ID}\",\"requested_map\":\"drain\",\"requested_game_mode\":\"ctf\"}"

curl -i -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches" \
  -H "content-type: application/json" \
  -d "{\"round_id\":\"${ROUND_ID}\",\"team_a_id\":\"${TEAM_A_ID}\",\"team_b_id\":\"${TEAM_B_ID}\",\"requested_map\":\"solarium\",\"requested_game_mode\":\"tdm\"}"
```

Allocate a persisted server assignment for that tournament match:

```bash
curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches/${MATCH_ID}/allocate-server" | jq
```

The response includes the PostgreSQL `assignment`, assigned endpoint, and configuration result. The backend allocates a warm Agones server, applies the match `requested_map` and `requested_game_mode` through the same whitelisted RCON configuration helper used by Match Rooms, and verifies with `getstatus`. The match is marked `server_ready` only when verification succeeds. If verification fails, the assignment remains persisted but the match is marked `failed` and the response includes a warning.

Verify the active assignment is persisted on the match:

```bash
curl -fsS "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches" | jq
curl -fsS "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches/${MATCH_ID}/server-assignments" | jq
```

Send a whitelisted admin control to the active tournament match assignment:

```bash
curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches/${MATCH_ID}/admin/broadcast" \
  -H "content-type: application/json" \
  -d '{"message":"Match starts in 2 minutes"}' | jq

curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches/${MATCH_ID}/admin/change-map" \
  -H "content-type: application/json" \
  -d '{"map":"stormkeep"}' | jq
```

Tournament match admin controls only target the active persisted assignment and use the same allowlisted RCON actions as Match Rooms.

Release the tournament match server assignment:

```bash
curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches/${MATCH_ID}/release-server" | jq
```

Release deletes the allocated Agones `GameServer`, marks the assignment `released`, preserves assignment history, and lets Fleet/FleetAutoscaler replenish standby capacity.

Record and finish the tournament match result:

```bash
curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches/${MATCH_ID}/result" \
  -H "content-type: application/json" \
  -d "{\"team_a_score\":12,\"team_b_score\":8,\"winner_team_id\":\"${TEAM_A_ID}\",\"result_notes\":\"Manual result after referee confirmation\"}" | jq
```

Result recording validates non-negative scores and requires the winner to be either `team_a_id` or `team_b_id`. It marks the match result saved, advances generated bracket winners into their configured next match slot when one exists, then releases the active match server assignment when present. If the backing Agones `GameServer` is already gone, the assignment is still marked released and the response includes a warning. The standalone `release-server` endpoint remains available for manual cleanup/debug cases.

The lower-level Match Room API below remains available for manual/operator server sessions and RCON controls.

Create a Match Room:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches \
  -H "content-type: application/json" \
  -d '{"name":"Quarterfinal 1","requested_map":"stormkeep","requested_game_mode":"dm"}'
```

List Match Rooms:

```bash
curl -fsS http://127.0.0.1:18080/matches
```

Get one Match Room:

```bash
curl -fsS http://127.0.0.1:18080/matches/<match_id>
```

Allocate a server for one Match Room:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/allocate \
  -H "content-type: application/json" \
  -d '{"requested_map":"stormkeep","requested_game_mode":"dm"}'
```

Allocation uses a warm Agones Fleet server. The backend sends `gametype <mode>` and `changelevel <map>` over RCON, verifies the live map/mode with `getstatus`, then returns the room as `joinable: true`. If verification fails, the room remains `allocated_needs_attention` and the endpoint should not be treated as ready for players.

Release a Match Room:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/release
```

Run the backend-only RCON smoke test for an allocated Match Room:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/rcon-smoke-test
```

Broadcast a message to an allocated Match Room:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/admin/broadcast \
  -H "content-type: application/json" \
  -d '{"message":"Match starts in 2 minutes"}'
```

Change an allocated Match Room to an allowlisted map:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/admin/change-map \
  -H "content-type: application/json" \
  -d '{"map":"stormkeep"}'
```

Terminate an allocated GameServer directly:

```bash
curl -fsS -X POST http://127.0.0.1:18080/allocated-servers/<gameserver_name>/terminate
```

The terminate endpoint validates that the `GameServer` exists in `xonotic-agones` and is currently `Allocated` before deleting it. If the server backs an in-memory Match Room, that room is marked released. Ready/standby servers are rejected.

Fields that are real now: `match_id`, `name`, `status`, `created_at`, `allocated_at`, `released_at`, `requested_map`, `requested_game_mode`, assigned server endpoint data, release result data, `joinable`, `allocation_config_result`, best-effort last-known-good `live_status` from Xonotic `getstatus`, `last_status_error`, and `change_map_verification`.

Fields that remain best-effort: `current_players`, `map`, player scores, and team scores. They are populated only after a server is allocated and responds to `getstatus`.

Example successful allocation response:

```json
{
  "allocation_request_name": null,
  "allocated_game_server_name": "xonotic-fleet-abcde-fghij",
  "address": "34.176.10.20",
  "port": 7003
}
```

## Runtime Configuration

- `AGONES_NAMESPACE`: defaults to `xonotic-agones`
- `FLEET_NAME`: defaults to `xonotic-fleet`
- `GAME_LABEL`: defaults to `xonotic`
- `ALLOCATION_TIMEOUT_SECONDS`: defaults to `5`
- `ALLOCATION_POLL_INTERVAL_SECONDS`: defaults to `0.25`
- `XONOTIC_STATUS_TIMEOUT_SECONDS`: defaults to `1`
- `XONOTIC_STATUS_CACHE_SECONDS`: defaults to `5`
- `XONOTIC_RCON_PASSWORD`: optional locally, required for RCON smoke test and admin-control endpoints
- `XONOTIC_RCON_TIMEOUT_SECONDS`: defaults to `2`
- `XONOTIC_RCON_OUTPUT_LIMIT`: defaults to `4000`
- `XONOTIC_RCON_CHANGE_MAP_STATUS_DELAY_SECONDS`: defaults to `1`
- `XONOTIC_RCON_CHANGE_MAP_VERIFY_TIMEOUT_SECONDS`: defaults to `12`
- `XONOTIC_RCON_CHANGE_MAP_VERIFY_INTERVAL_SECONDS`: defaults to `1`
- `XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG`: defaults to `0`; set to `1` only during controlled map/mode verification probes
- `DEFAULT_MATCH_MAX_PLAYERS`: defaults to `8`; planning metadata only, not enforced on warm Fleet servers
- `MAX_MATCH_PLAYERS_LIMIT`: defaults to `32`; validation limit for that metadata
- `DATABASE_URL`: optional full PostgreSQL connection URL; overrides individual PostgreSQL settings when set
- `POSTGRES_HOST`: PostgreSQL host when `DATABASE_URL` is not set
- `POSTGRES_PORT`: defaults to `5432`
- `POSTGRES_DB`: PostgreSQL database name
- `POSTGRES_USER`: PostgreSQL username
- `POSTGRES_PASSWORD`: PostgreSQL password
- `POSTGRES_CONNECT_TIMEOUT_SECONDS`: defaults to `3`
