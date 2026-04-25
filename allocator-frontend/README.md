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
- Match Room creation and allocation
- Fleet summary
- current `GameServer` list
- direct/manual allocation as a lower-level debug action

Match Rooms are the primary admin-facing objects. Allocated `GameServer` instances are the infrastructure backing those rooms. `Ready` servers are standby/internal capacity and are not presented as join targets.

## Backend Endpoints Used

- `GET /healthz`
- `GET /fleet-status`
- `GET /gameservers`
- `GET /matches`
- `POST /matches`
- `POST /matches/<match_id>/allocate`
- `POST /allocate`
