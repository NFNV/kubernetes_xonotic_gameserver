# Xonotic Kubernetes Game Server Platform

A Kubernetes-native platform for deploying and operating dedicated Xonotic game servers, using Agones on GKE. The project includes an admin control plane for tournament operations: allocating match servers, configuring map/mode through RCON, verifying live server state, recording results, and cleaning up resources.

The goal is not to build a generic tournament bracket app; the goal is to demonstrate the infrastructure and operational workflows behind running dedicated multiplayer game servers on Kubernetes. Tournament management is the practical use case that exercises the platform: server allocation, lifecycle automation, live verification, persistence, observability, and resource cleanup.

## Architecture Overview

```text
Terraform / GKE / firewall rules
  -> Agones Fleet + FleetAutoscaler
  -> Ready Xonotic GameServers on dynamic UDP ports
  -> Allocator Backend
  -> PostgreSQL
  -> Admin / Player UI
  -> RCON configuration + getstatus verification
  -> Prometheus metrics
  -> Grafana dashboard
```

The system is organized into three planes.

### Infrastructure Plane

- **Terraform** provisions the GKE foundation and cloud networking.
- **GKE Standard** hosts the platform and Xonotic GameServer workloads.
- **Agones** manages dedicated GameServer lifecycle.
- **Fleet/FleetAutoscaler** keep a small buffer of ready Xonotic servers available.
- **Firewall rules and dynamic UDP ports** allow allocated GameServers to be reached by players.
- **GHCR** stores the server, backend, and frontend container images.

### Control Plane

- **Allocator backend:** Flask service that talks to Kubernetes/Agones, stores tournament state, configures servers, verifies live state, and exposes operational APIs.
- **PostgreSQL:** stores tournaments, teams, rounds, matches, results, and match server assignment history.
- **React Admin UI:** operator interface for allocation, tournament workflow, result recording, finalization, and lower-level debug controls.
- **React Player View:** read-only tournament view for players/spectators, including live endpoints and copyable `connect IP:PORT` commands.
- **RCON/getstatus integration:** configures map/mode and verifies that the allocated server is actually running the expected live configuration.

### Observability Plane

- **Prometheus** scrapes backend metrics.
- **Grafana** visualizes allocator health and tournament/server operations.
- **Backend metrics** track request behavior, allocation outcomes, active assignments, RCON failures, and verification failures.
- **Kubernetes/GameServer capacity visibility** helps debug no-ready-server conditions and resource pressure.

## What This Project Demonstrates

- Kubernetes-native game server deployment
- Agones Fleet and FleetAutoscaler operation
- Dynamic UDP GameServer allocation
- Admin control plane for dedicated server lifecycle
- Match and tournament workflows as a real operational use case
- RCON-based server configuration with live `getstatus` verification
- PostgreSQL-backed platform state and assignment history
- Prometheus/Grafana observability for allocation and platform health
- Terraform, GKE, GHCR, and scripts for infrastructure automation and cost control

The target concept is worldwide tournament server management: operators can create tournament matches, allocate dedicated servers, configure and verify them, expose connection commands to players, record results, advance winners, and clean up GameServer resources.

## Game Server Allocation Lifecycle

1. The Agones Fleet keeps warm Xonotic GameServers in the `Ready` state.
2. The admin control plane requests a `GameServerAllocation`.
3. Agones assigns a ready GameServer and exposes its dynamic UDP endpoint.
4. The backend persists the assignment in PostgreSQL.
5. The backend configures the requested map/mode through whitelisted RCON commands.
6. The backend verifies the live server state through `getstatus`.
7. The UI exposes the endpoint and `connect IP:PORT` command only after verification.
8. The server is released manually or automatically during tournament finalization.

This makes allocation more than “start a pod.” The platform reserves a real dedicated game server, applies match configuration, validates live state, records ownership, and releases capacity when the match or tournament lifecycle is complete.

## Tournament Operations Built On The Platform

The tournament system is the operational workflow layered on top of the game server platform.

Current tournament capabilities include:

- Tournament creation
- Team management with manual seeding
- Single-elimination bracket generation
- Persisted rounds and matches
- Match server allocation through Agones
- Verified map/mode selection
- Result recording
- Winner advancement through the bracket
- Tournament finalization
- Automatic active server cleanup on finalize
- Player-facing read-only tournament view
- Admin view for allocation, result, release, and debug workflows

Tournament lifecycle:

```text
create tournament
  -> add teams
  -> generate bracket
  -> allocate match server
  -> configure and verify map/mode
  -> play match
  -> record result
  -> winner advances
  -> final result recorded
  -> finalize tournament
  -> active servers released
```

Finalization is deliberate: the final match must have a recorded winner. If active match server assignments still exist, finalization releases the corresponding Agones GameServers and marks those assignments released before completing the tournament. This prevents completed tournaments from silently consuming Fleet capacity.

## Admin And Player Experience

The frontend is not the core product by itself; it is the control surface for the platform.

- **Admin View:** focuses on tournament operations, match allocation, result recording, server release, Fleet capacity, and lower-level debugging.
- **Player View:** read-only presentation of tournament status, rounds, matches, results, winners, active server endpoint, and copyable Xonotic connect command.

Advanced/manual Match Room controls remain available for platform testing, but the normal workflow is persisted tournament matches backed by Agones GameServer assignments.

## Observability

The allocator backend exposes Prometheus metrics at `/metrics`. Prometheus scrapes the backend, and Grafana provides a lightweight dashboard for platform health.

Metrics include:

- HTTP request count and latency
- Allocation attempts, successes, and failures
- Active match server assignments
- RCON command attempts and failures
- Map/mode verification successes and failures

These signals help diagnose operational problems such as:

- no `Ready` Xonotic GameServers available
- Fleet/FleetAutoscaler capacity lag
- failed GameServer allocations
- RCON command failures
- live map/mode verification failures
- pod or node resource pressure in a small dev cluster

## Kubernetes / GKE Setup

The project targets a small GKE Standard development cluster:

- Terraform provisions the GKE cluster and required firewall rules.
- Agones manages Xonotic GameServer lifecycle.
- The Xonotic Fleet uses dynamic UDP ports for allocated servers.
- Backend, frontend, PostgreSQL, Prometheus, and Grafana run as Kubernetes workloads.
- Images are built and published to GHCR.
- `scripts/up.sh` and `scripts/down.sh` help bring infrastructure up/down for cost control.
- Resource requests/limits and `Recreate` rollout strategy are used to fit a constrained single-node dev cluster.

This is a portfolio-grade development platform, not a production-hardened service. The implementation is intentionally scoped to demonstrate platform engineering, game server orchestration, lifecycle automation, and observability without overclaiming production readiness.

## Screenshots

![Admin Dashboard](docs/screenshots/admin-dashboard.png)

![Player View](docs/screenshots/player-view.png)

![Grafana Dashboard](docs/screenshots/grafana-dashboard.png)

![Kubernetes / Agones Status](docs/screenshots/kubernetes-agones-status.png)

## Local / Dev Usage

Copy the environment template and fill in local project values:

```bash
cp scripts/env.sh.example scripts/env.sh
```

Configure GCP project, region, zone, and local development values in `scripts/env.sh`. This file is intentionally ignored because it contains machine-specific configuration and secrets.

Bring the dev platform up:

```bash
source scripts/env.sh
./scripts/up.sh
```

Port-forward common services:

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
- Concurrent match capacity is limited by Fleet settings and node resources.
- There is no production authentication or authorization layer yet.
- There is no public domain or Ingress path yet.
- PostgreSQL runs in-cluster and is dev-grade.
- The platform is not multi-region.
- Alerts and runbooks are minimal.
- Capacity depends on both Agones Fleet availability and underlying Kubernetes resources.
- The current implementation demonstrates the control and orchestration workflows before production hardening.

## Future Work

- Admin authentication and role separation
- Public Ingress and domain setup
- Managed PostgreSQL or backup/restore workflow
- Additional tournament formats beyond single elimination
- Matchmaking or match request queue
- More advanced Fleet autoscaling and scheduling strategies
- Alerting for allocation failures, RCON failures, and capacity pressure
- Multi-node and multi-region support
- CI/CD deployment automation from GitHub to GKE

## Repository Map

- `infra/`: Terraform for the GCP/GKE foundation
- `server/`: Xonotic dedicated server image and runtime configuration
- `allocator-backend/`: Flask allocator and platform API
- `allocator-frontend/`: React admin/player control surface
- `platform/agones/`: Agones Fleet and FleetAutoscaler manifests
- `platform/postgres/`: PostgreSQL development manifests
- `platform/allocator-backend/`: backend Kubernetes manifests
- `platform/allocator-frontend/`: frontend Kubernetes manifests
- `platform/observability/`: lightweight Prometheus/Grafana manifests
- `docs/`: design notes and verification workflows
- `scripts/`: local bring-up/tear-down and verification helpers
