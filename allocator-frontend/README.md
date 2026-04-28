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
- Fleet summary
- current `GameServer` list
- direct/manual allocation as a lower-level debug action

Match Rooms are the primary admin-facing objects. Allocated `GameServer` instances are the infrastructure backing those rooms. `Ready` servers are standby/internal capacity and are not presented as join targets.

Map, mode, and max-player controls are intentionally deferred. In the current warm Fleet model, servers are already running before allocation, so the UI shows live server status instead of presenting non-enforced config as a real control feature.

## Backend Endpoints Used

- `GET /healthz`
- `GET /fleet-status`
- `GET /gameservers`
- `GET /matches`
- `POST /matches`
- `POST /matches/<match_id>/allocate`
- `POST /matches/<match_id>/release`
- `POST /allocate`
