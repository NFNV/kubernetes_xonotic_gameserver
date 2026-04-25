# Allocator Backend

This directory contains the first in-cluster allocator backend for the project.

It is intentionally small:

- Python
- one HTTP process
- no database
- no auth
- simple JSON API that can be consumed by the operator frontend

The service runs inside Kubernetes and uses the Kubernetes API directly to create and read `GameServerAllocation` resources in `xonotic-agones`.

That is the right choice for this phase because the backend already runs in-cluster and only needs the simplest possible path to allocate from the existing Agones Fleet.

## Match Rooms

Match Rooms are the first admin-facing layer above raw Agones allocation.

A Match Room represents an operator-created match/session. It can have one allocated Xonotic `GameServer` assigned to it. The allocated `GameServer` is the infrastructure backing the room; standby `Ready` servers remain internal capacity.

Match Rooms are stored only in backend process memory for now. They disappear when the backend Pod restarts. That is intentional for this MVP because there is no database, auth, player account model, or tournament bracket logic yet.

## API

- `GET /healthz`
- `GET /fleet-status`
- `GET /gameservers`
- `POST /matches`
- `GET /matches`
- `GET /matches/<match_id>`
- `POST /matches/<match_id>/allocate`
- `POST /allocate`

`POST /allocate` remains available as a direct/manual allocation test endpoint. The operator UI should prefer Match Rooms.

Create a Match Room:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches \
  -H "content-type: application/json" \
  -d '{"name":"Quarterfinal 1","max_players":8,"game_mode":"dm"}'
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

Fields that are real now: `match_id`, `name`, `status`, `created_at`, `allocated_at`, `max_players`, `game_mode`, and assigned server endpoint data.

Fields that are placeholders until later backend/game telemetry exists: `current_players` and `map`.

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
