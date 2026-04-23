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
- Fleet summary
- current `GameServer` list
- one "Allocate Server" action
- latest allocation result

## Backend Endpoints Used

- `GET /healthz`
- `GET /fleet-status`
- `GET /gameservers`
- `POST /allocate`
