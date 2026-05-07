# Allocator Frontend

This directory contains the small React-based admin frontend for the allocator backend.

It is intentionally narrow:

- React
- static build served by nginx
- `/api` proxied to the in-cluster allocator backend
- no auth
- no persistence

## UI Scope

- backend health status
- Match Room creation
- Match Room allocation and release
- live player/map/score status for allocated Match Rooms
- whitelisted admin controls for allocated Match Rooms: broadcast message and change map
- allocated-server infrastructure controls: command panel and terminate
- Fleet summary
- current `GameServer` list
- direct/manual allocation as a collapsed advanced/debug action

Match Rooms are the primary admin-facing objects. Allocated `GameServer` instances are the infrastructure backing those rooms. `Ready` servers are standby/internal capacity and are not presented as join targets.

Game mode and max-player controls are intentionally deferred. The current admin controls are intentionally narrow: broadcast a message with RCON `say` and change to an allowlisted map with RCON `changelevel`. The UI does not expose raw RCON command input or the RCON password.

Manual Direct Allocation is hidden under Advanced / Debug and should only be used for lower-level allocator testing.

Allocated Servers are infrastructure allocations. The table can terminate an `Allocated` GameServer directly; Fleet/FleetAutoscaler should replenish capacity afterward. The Commands panel routes safe actions through a linked Match Room when available and keeps direct/manual allocations disabled for RCON actions.

## Backend Endpoints Used

- `GET /healthz`
- `GET /fleet-status`
- `GET /gameservers`
- `GET /matches`
- `POST /matches`
- `POST /matches/<match_id>/allocate`
- `POST /matches/<match_id>/release`
- `POST /matches/<match_id>/admin/broadcast`
- `POST /matches/<match_id>/admin/change-map`
- `POST /allocated-servers/<gameserver_name>/terminate`
- `POST /allocate`
