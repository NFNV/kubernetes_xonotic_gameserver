# Xonotic Kubernetes Game Server Platform

A Kubernetes-native platform for deploying and operating dedicated Xonotic game servers with Agones on GKE. It includes an admin control plane for allocating match servers, configuring map/mode through RCON, verifying live state with `getstatus`, running tournament workflows, observing platform health, and cleaning up resources.

The goal is not to build a generic tournament bracket app. The tournament workflow is the practical use case for demonstrating the infrastructure and operations behind dedicated multiplayer game servers on Kubernetes.

## Architecture

```text
Terraform + GKE + firewall rules
  -> Agones Fleet + FleetAutoscaler
  -> Xonotic GameServers on dynamic UDP ports
  -> Flask allocator backend + PostgreSQL
  -> React Admin / Player UI
  -> RCON configuration + getstatus verification
  -> Prometheus metrics + Grafana dashboard
```

### Infrastructure Plane

- Terraform provisions GKE and cloud networking.
- Agones manages Xonotic GameServer lifecycle.
- Fleet/FleetAutoscaler keep a small buffer of ready servers.
- Dynamic UDP ports and firewall rules expose allocated GameServers.
- GHCR stores server, backend, and frontend images.

### Control Plane

- Flask allocator backend handles Agones allocation, server release, RCON configuration, `getstatus` verification, and API workflows.
- PostgreSQL stores tournaments, teams, rounds, matches, results, and server assignment history.
- React Admin View manages allocation, tournament workflow, result recording, finalization, and debug controls.
- React Player View exposes read-only match status, results, endpoints, and copyable `connect IP:PORT` commands.

### Observability Plane

- Prometheus scrapes backend `/metrics`.
- Grafana visualizes allocator health and server/tournament operations.
- Metrics track HTTP behavior, allocation outcomes, active assignments, RCON failures, and map/mode verification.

## Features

- Kubernetes-native Xonotic dedicated server deployment on GKE
- Agones Fleet and FleetAutoscaler for warm GameServer capacity
- Dynamic UDP GameServer allocation through Agones
- Flask admin control plane for server lifecycle operations
- PostgreSQL-backed tournament, match, result, and assignment state
- Verified map/mode selection with RCON configuration and `getstatus` validation
- React Admin View for operators and read-only Player View for players/spectators
- Single-elimination tournament workflow with result recording and winner advancement
- Tournament finalization with automatic active GameServer cleanup
- Prometheus/Grafana observability for allocation and platform health
- Terraform, GHCR publishing, and dev scripts for infrastructure automation and cost control

The target concept is worldwide tournament server management: operators allocate dedicated servers, configure and verify them, expose player connection commands, record results, and release capacity when matches are complete.

## Game Server Allocation Lifecycle

1. The Agones Fleet keeps Xonotic GameServers warm in the `Ready` state.
2. The control plane requests a `GameServerAllocation`.
3. Agones assigns a ready server and exposes its dynamic UDP endpoint.
4. The backend persists the assignment in PostgreSQL.
5. The backend configures map/mode through whitelisted RCON commands.
6. The backend verifies live server state with `getstatus`.
7. The UI exposes the endpoint and `connect IP:PORT` only after verification.
8. The server is released manually or during tournament finalization.

Allocation here means reserving, configuring, verifying, tracking, and eventually releasing a real dedicated game server, not merely starting a pod.

## Tournament Operations

Tournament operations are built on top of the game server platform:

```text
create tournament -> add teams -> generate bracket -> allocate match server
  -> configure/verify map and mode -> play match -> record result
  -> advance winner -> finalize tournament -> release active servers
```

Current tournament features include team management, manual seeding, single-elimination bracket generation, persisted rounds/matches, result recording, winner advancement, finalization, and player-facing read-only match views.

Finalization requires a recorded winner for the final match. If active match server assignments still exist, finalization releases the corresponding Agones GameServers and marks those assignments released before completing the tournament, preventing completed events from consuming Fleet capacity.

## Observability

The backend exposes Prometheus metrics at `/metrics`, and Grafana provides a lightweight dashboard for platform health. Metrics cover request count/latency, allocation attempts/successes/failures, active match server assignments, RCON command failures, and map/mode verification failures.

These signals help diagnose issues such as no ready GameServers, FleetAutoscaler capacity lag, failed allocations, RCON problems, verification failures, and resource pressure on the small dev cluster.

## Kubernetes / GKE Setup

This project targets a small GKE Standard development cluster:

- Terraform provisions the cluster and firewall rules.
- Agones runs the Xonotic Fleet with dynamic UDP ports.
- Backend, frontend, PostgreSQL, Prometheus, and Grafana run as Kubernetes workloads.
- Images are built and published to GHCR.
- `scripts/up.sh` and `scripts/down.sh` help control cloud costs.
- Resource requests/limits and `Recreate` rollout strategy are used for a constrained single-node dev cluster.

This is a portfolio-grade development platform, not a production-hardened service.

## Screenshots

![Admin Dashboard](docs/screenshots/admin-dashboard.png)

![Player View](docs/screenshots/player-view.png)

![Grafana Dashboard](docs/screenshots/grafana-dashboard.png)

![Kubernetes / Agones Status](docs/screenshots/kubernetes-agones-status.png)

## Local / Dev Usage

```bash
cp scripts/env.sh.example scripts/env.sh
```

Configure GCP project, region, zone, and local values in `scripts/env.sh`. This file is intentionally ignored because it contains local configuration and secrets.

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

- Frontend: `http://127.0.0.1:18080`
- Backend: `http://127.0.0.1:18082`
- Grafana: `http://127.0.0.1:3000`

Tear down cloud resources:

```bash
./scripts/down.sh
```

## Limitations

- Single-node GKE dev cluster
- Limited concurrent match capacity
- No production auth yet
- No public domain or Ingress yet
- In-cluster PostgreSQL is dev-grade
- Not multi-region
- Minimal alerts/runbooks
- Capacity depends on Fleet/FleetAutoscaler and Kubernetes node resources

## Future Work

- Admin authentication and role separation
- Public Ingress/domain
- Managed PostgreSQL or backups
- Additional tournament formats
- Matchmaking or match request queue
- Improved Fleet autoscaling and scheduling
- Alerts for allocation, RCON, verification, and capacity issues
- Multi-node and multi-region support
- CI/CD deployment automation from GitHub to GKE

## Repository Map

- `infra/`: Terraform for GCP/GKE
- `server/`: Xonotic server image and runtime config
- `allocator-backend/`: Flask allocator and platform API
- `allocator-frontend/`: React admin/player UI
- `platform/agones/`: Agones Fleet/FleetAutoscaler manifests
- `platform/postgres/`: PostgreSQL dev manifests
- `platform/observability/`: Prometheus/Grafana manifests
- `scripts/`: local bring-up, tear-down, and verification helpers
