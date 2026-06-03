# Xonotic Tournament Platform on Kubernetes

This repository is a Kubernetes-native multiplayer game server platform for running Xonotic tournament matches on GKE. It uses Agones for dedicated GameServer orchestration, a custom Flask allocator backend, a React admin/player UI, PostgreSQL persistence, and Prometheus/Grafana observability.

The project is a DevOps and Platform Engineering portfolio case study. It focuses on game server lifecycle automation, tournament operations, capacity-aware allocation, live server verification, and practical infrastructure automation for a small GKE development environment.

## Architecture Overview

```text
Admin / Player UI
  -> Allocator Backend
  -> PostgreSQL
  -> Agones GameServerAllocation
  -> Xonotic GameServer Fleet
  -> RCON / getstatus verification
  -> Prometheus metrics
  -> Grafana dashboard
```

The platform has two main planes:

- **Control plane:** React frontend, Flask allocator backend, PostgreSQL, Prometheus, and Grafana.
- **Game server plane:** Agones Fleet, FleetAutoscaler, and Xonotic dedicated GameServers with dynamic UDP ports.

The frontend is split into an operator-focused Admin View and a read-only Player View. The backend owns allocation, persistence, server release, RCON configuration, result recording, bracket advancement, finalization, and metrics.

## Features

- Tournament creation and management
- Team management with manual seeding
- Single-elimination bracket generation
- Persisted tournament rounds and matches
- Match server allocation through Agones
- Verified map/mode selection
- RCON-based map and mode configuration
- Live `getstatus` verification before exposing join endpoints
- Match result recording
- Winner advancement through the bracket
- Tournament finalization
- Automatic active server cleanup during finalization
- Player View with match status, scores, winner, server endpoint, and copyable `connect IP:PORT` command
- Admin View with allocation, result, release, and lower-level debug controls
- Prometheus metrics exposed by the backend
- Grafana dashboard for allocator and tournament operations

## How Server Allocation Works

1. The Agones Fleet keeps a small pool of `Ready` Xonotic GameServers warm.
2. The allocator backend requests a `GameServerAllocation`.
3. Agones assigns one ready server and marks it allocated.
4. The backend stores the tournament match assignment in PostgreSQL.
5. The backend configures the requested map and mode through whitelisted RCON commands.
6. The backend verifies the live server state with `getstatus`.
7. Once verification passes, the UI exposes the endpoint and `connect IP:PORT` command.
8. The server is released manually from the Admin View or automatically during tournament finalization.

This flow avoids exposing a server to players until the backend has both allocated it and confirmed that it is running the expected match configuration.

## Tournament Lifecycle

```text
create tournament
  -> add teams
  -> generate bracket
  -> allocate match server
  -> play match
  -> record result
  -> winner advances
  -> final result recorded
  -> finalize tournament
  -> active servers released
```

Finalization is deliberate: the final match must have a recorded winner. If the tournament still has active match server assignments, finalization releases those GameServers and marks the assignments released before completing the tournament. This prevents completed tournaments from silently consuming Fleet capacity.

## Observability

The allocator backend exposes Prometheus metrics at `/metrics`. A lightweight Prometheus deployment scrapes the backend, and Grafana is provisioned with a basic dashboard for allocator health and tournament operations.

Current metrics cover:

- HTTP request count and latency
- Allocation attempts, successes, and failures
- Active match server assignments
- RCON command attempts and failures
- Map/mode verification successes and failures

These signals help debug operational issues such as no ready GameServers, allocation failures, RCON problems, map/mode verification failures, and resource pressure on the small development cluster.

## Kubernetes / GKE Setup

The platform is designed for a small GKE Standard development cluster:

- Terraform provisions the GKE foundation and required networking/firewall rules.
- Agones manages Xonotic GameServer lifecycle.
- The backend, frontend, PostgreSQL, Prometheus, and Grafana run as Kubernetes workloads.
- Container images are published to GHCR.
- `scripts/up.sh` and `scripts/down.sh` support low-cost bring-up and teardown.
- Workloads use explicit resource requests/limits.
- Admin workloads use a `Recreate` deployment strategy to fit a constrained single-node dev cluster.

This is not presented as a production-ready system. It is intentionally scoped as a practical platform engineering demo that exercises real orchestration, lifecycle, persistence, and observability concerns.

## Screenshots

![Admin Dashboard](docs/screenshots/admin-dashboard.png)

![Player View](docs/screenshots/player-view.png)

![Grafana Dashboard](docs/screenshots/grafana-dashboard.png)

![Kubernetes / Agones Status](docs/screenshots/kubernetes-agones-status.png)

## Local / Dev Usage

Copy the local environment template and fill in project-specific values:

```bash
cp scripts/env.sh.example scripts/env.sh
```

Configure the GCP project, region, zone, and local development values in `scripts/env.sh`. The file is intentionally ignored by git because it contains machine-specific configuration and secrets.

Bring the dev platform up:

```bash
source scripts/env.sh
./scripts/up.sh
```

Port-forward the main services:

```bash
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-frontend 18080:8080
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-backend 18082:8080
kubectl port-forward -n xonotic-observability service/xonotic-grafana 3000:3000
```

Typical local URLs:

- Frontend: `http://127.0.0.1:18080`
- Backend: `http://127.0.0.1:18082`
- Grafana: `http://127.0.0.1:3000`

Tear down cloud resources when finished:

```bash
./scripts/down.sh
```

## Tradeoffs / Limitations

- The current environment targets a single-node GKE development cluster.
- Concurrent match capacity is intentionally limited by node resources and Fleet/FleetAutoscaler settings.
- There is no production authentication or authorization layer yet.
- There is no public domain or Ingress path yet.
- PostgreSQL runs in-cluster and is suitable for development, not production durability.
- The platform is not multi-region.
- Alerts and runbooks are minimal.
- Capacity depends on both Agones Fleet availability and underlying node resources.
- Lower-level Match Room/debug tooling still exists for operator testing, but normal tournament workflow should use persisted tournament matches.

## Future Work

- Admin authentication and role separation
- Public Ingress and domain setup
- Managed PostgreSQL or backup/restore workflow
- Additional bracket formats beyond single elimination
- Matchmaking or sign-up queue
- More advanced Fleet autoscaling and scheduling strategies
- Alerting for allocation failures, RCON failures, and capacity pressure
- Multi-node and eventually multi-region support
- CI/CD deployment automation from GitHub to the dev cluster

## Repository Map

- `infra/`: Terraform for the GCP/GKE foundation
- `server/`: Xonotic dedicated server image and runtime configuration
- `allocator-backend/`: Flask allocator and tournament API
- `allocator-frontend/`: React admin/player UI
- `platform/agones/`: Agones Fleet and FleetAutoscaler manifests
- `platform/postgres/`: PostgreSQL development manifests
- `platform/allocator-backend/`: backend Kubernetes manifests
- `platform/allocator-frontend/`: frontend Kubernetes manifests
- `platform/observability/`: lightweight Prometheus/Grafana manifests
- `docs/`: design notes and verification workflows
- `scripts/`: local bring-up/tear-down and verification helpers
