# Allocator Backend

This directory contains the first in-cluster allocator backend for the project.

It is intentionally small:

- Python
- one HTTP process
- no database
- no auth
- no frontend coupling

The service runs inside Kubernetes and uses the Kubernetes API directly to create and read `GameServerAllocation` resources in `xonotic-agones`.

That is the right choice for this phase because the backend already runs in-cluster and only needs the simplest possible path to allocate from the existing Agones Fleet.

## API

- `GET /healthz`
- `POST /allocate`

Example successful allocation response:

```json
{
  "allocation_name": "xonotic-allocation-abcde",
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
