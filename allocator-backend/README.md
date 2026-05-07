# Allocator Backend

This directory contains the first in-cluster allocator backend for the project.

It is intentionally small:

- Python
- one HTTP process
- no database
- no auth
- simple JSON API that can be consumed by the operator frontend

The service runs inside Kubernetes and uses the Kubernetes API directly to create/read `GameServerAllocation` resources and delete allocated `GameServer` resources in `xonotic-agones`.

That is the right choice for this phase because the backend already runs in-cluster and only needs the simplest possible path to allocate from the existing Agones Fleet.

## Match Rooms

Match Rooms are the first admin-facing layer above raw Agones allocation.

A Match Room represents an operator-created match/session and can have one allocated Xonotic `GameServer` assigned to it. The allocated `GameServer` is the infrastructure backing the room; standby `Ready` servers remain internal capacity.

Match Rooms are stored only in backend process memory for now. They disappear when the backend Pod restarts. That is intentional for this MVP because there is no database, auth, player account model, or tournament bracket logic yet.

For allocated rooms, the backend also sends a read-only UDP `getstatus` query to the assigned Xonotic server. That response is cached briefly and used to fill live player count, map, game mode, player names, scores, ping, and team score data when available. This does not use RCON.

Per-match game mode and max-player controls are intentionally deferred. The current RCON admin-control phase supports only two whitelisted live actions for allocated rooms: broadcast a server message and change to an allowlisted map. Live status remains the source of truth for the running server.

Releasing a Match Room deletes the allocated Agones `GameServer` resource, removes the user-facing endpoint from the room, and lets the Fleet/FleetAutoscaler create replacement standby capacity.

## API

- `GET /healthz`
- `GET /fleet-status`
- `GET /gameservers`
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

Create a Match Room:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches \
  -H "content-type: application/json" \
  -d '{"name":"Quarterfinal 1"}'
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
curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/allocate
```

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

Fields that are real now: `match_id`, `name`, `status`, `created_at`, `allocated_at`, `released_at`, assigned server endpoint data, release result data, best-effort last-known-good `live_status` from Xonotic `getstatus`, `last_status_error`, and `change_map_verification`.

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
- `DEFAULT_MATCH_MAX_PLAYERS`: defaults to `8`; planning metadata only, not enforced on warm Fleet servers
- `MAX_MATCH_PLAYERS_LIMIT`: defaults to `32`; validation limit for that metadata
