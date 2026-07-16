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
  -> Prometheus metrics + Loki logs + Grafana dashboards
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
- React Admin View is password-protected and manages allocation, tournament workflow, result recording, finalization, and debug controls.
- React Player View exposes read-only match status, results, endpoints, and copyable `connect IP:PORT` commands.

### Observability Plane

- Prometheus scrapes backend and Kubernetes infrastructure metrics.
- Grafana Alloy collects primary-cluster pod logs and forwards them to Loki.
- Grafana visualizes allocator metrics, cluster health, and operational logs.

## Features

- Kubernetes-native Xonotic dedicated server deployment on GKE
- Agones Fleet and FleetAutoscaler for warm GameServer capacity
- Dynamic UDP GameServer allocation through Agones
- Flask admin control plane for server lifecycle operations
- PostgreSQL-backed tournament, match, result, and assignment state
- Verified map/mode selection with RCON configuration and `getstatus` validation
- Password-protected React Admin View for operators and public read-only Player View for players/spectators
- Admin server-pool capacity visibility for Ready/Allocated regional game server capacity
- Single-elimination tournament workflow with result recording and winner advancement
- Tournament finalization with automatic active GameServer cleanup
- Prometheus metrics, Loki logs, Alloy collection, and Grafana dashboards for platform health
- Terraform, GHCR publishing, and dev scripts for infrastructure automation and cost control

The target concept is worldwide tournament server management: operators allocate dedicated servers, configure and verify them, expose player connection commands, record results, and release capacity when matches are complete.

The platform uses a central South America control plane with provisioned South America, Europe, and North America Agones server pools. The backend selects a regional Kubernetes context for allocation, capacity checks, and GameServer release while keeping PostgreSQL and operator services centralized.

## Game Server Allocation Lifecycle

1. The Agones Fleet keeps Xonotic GameServers warm in the `Ready` state.
2. The control plane requests a `GameServerAllocation`.
3. Agones assigns a ready server and exposes its dynamic UDP endpoint.
4. The backend persists the assignment in PostgreSQL.
5. The backend configures map/mode through whitelisted RCON commands.
6. The backend verifies live server state with `getstatus`.
7. The UI exposes the endpoint and `connect IP:PORT` only after verification.
8. The server is released automatically when the match result is recorded, or manually/finalization cleanup handles leftovers.

Allocation here means reserving, configuring, verifying, tracking, and eventually releasing a real dedicated game server, not merely starting a pod.

## Tournament Operations

Tournament operations are built on top of the game server platform:

```text
create tournament -> add teams -> generate bracket -> allocate match server
  -> configure/verify map and mode -> play match -> record result
  -> release match server -> advance winner -> finalize tournament
```

Current tournament features include team management, manual seeding, single-elimination bracket generation, persisted rounds/matches, result recording with automatic match server cleanup, winner advancement, finalization, and player-facing read-only match views.

Finalization requires a recorded winner for the final match. Result recording closes the match server automatically; if active match server assignments still exist, finalization releases the corresponding Agones GameServers and marks those assignments released before completing the tournament, preventing completed events from consuming Fleet capacity.

## Observability

Prometheus collects metrics, Loki stores short-lived Kubernetes pod logs, and Grafana provides one place to explore both. Grafana Alloy runs on each primary-cluster node and forwards labeled backend, Agones, and Xonotic logs to Loki. Metrics cover request count/latency, allocation outcomes, active assignments, RCON failures, verification failures, and Kubernetes resource pressure; log panels focus on backend activity, allocation failures, RCON/`getstatus` errors, and GameServer output.

The logging setup is intentionally dev-grade: Loki uses bounded ephemeral storage with 24-hour retention, remains reachable only inside the cluster or through port-forwarding, and currently observes the primary South America cluster only.

## Kubernetes / GKE Setup

This project targets a small GKE Standard development cluster:

- Terraform provisions the cluster and firewall rules.
- Agones runs the Xonotic Fleet with dynamic UDP ports.
- Backend, frontend, PostgreSQL, Prometheus, Loki, Alloy, and Grafana run as Kubernetes workloads.
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

Generate admin auth values locally and place them in `scripts/env.sh`:

```bash
scripts/generate-admin-auth.sh --username admin --password admin
```

Paste the generated `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, and `ADMIN_SESSION_SECRET` export lines into `scripts/env.sh`. Keep the generated single quotes around `ADMIN_PASSWORD_HASH`; Werkzeug hashes contain `$` separators. `scripts/up.sh` validates these values before deployment and recreates the `xonotic-admin-auth` Kubernetes Secret in the allocator namespace on every deploy, so Admin View auth survives backend/frontend Pod restarts and is restored after down/up cycles. Player View remains public and read-only.

Verify the Secret exists without printing secret values:

```bash
kubectl get secret xonotic-admin-auth -n xonotic-allocator-backend \
  -o go-template='{{range $k, $_ := .data}}{{println $k}}{{end}}'
```

```bash
source scripts/env.sh
./scripts/up.sh
```

`./scripts/up.sh` is the single primary-environment entrypoint. It selects the `south-america` Terraform workspace and brings up the South America GKE/Agones game-server plane plus PostgreSQL, allocator backend, allocator frontend, and lightweight Prometheus/Grafana/Loki observability. Observability deploys automatically and warns without blocking the primary environment if it cannot roll out. Europe and North America remain game-server-only regional deployments:

```bash
./scripts/up-region.sh europe
./scripts/up-region.sh north-america
```

The backend deployment requires the South America context in its generated kubeconfig and includes Europe/North America when they are reachable. From a fully stopped environment, bring up the game-server-only regions first if you want all regional pools available immediately, then the primary environment:

```bash
./scripts/up-region.sh europe &&
./scripts/up-region.sh north-america &&
./scripts/up.sh
```

If regional credentials or the mounted Secret become stale, refresh and reconcile them without manually constructing a Secret:

```bash
gcloud container clusters get-credentials xonotic-mvp --zone southamerica-west1-a --project "${GCP_PROJECT_ID}"
gcloud container clusters get-credentials xonotic-eu --zone europe-west1-b --project "${GCP_PROJECT_ID}"
gcloud container clusters get-credentials xonotic-na --zone us-central1-a --project "${GCP_PROJECT_ID}"
./scripts/build-multicluster-kubeconfig.sh
./scripts/apply-multicluster-kubeconfig-secret.sh
kubectl rollout restart deployment/xonotic-allocator-backend -n xonotic-allocator-backend
```

For repositories upgraded from the earlier default-workspace flow, `up.sh` detects an existing South America cluster, node pool, and UDP firewall rules and imports missing bindings into the `south-america` workspace before applying. This avoids duplicate-resource `409 Already exists` failures.

Port-forward common services:

```bash
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-frontend 18080:8080
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-backend 18082:8080
kubectl port-forward -n xonotic-observability service/xonotic-prometheus 9090:9090
kubectl port-forward -n xonotic-observability service/xonotic-loki 3100:3100
kubectl port-forward -n xonotic-observability service/xonotic-grafana 3000:3000
```

- Frontend: `http://127.0.0.1:18080`
- Backend: `http://127.0.0.1:18082`
- Prometheus: `http://127.0.0.1:9090`
- Loki readiness/API: `http://127.0.0.1:3100/ready`
- Grafana: `http://127.0.0.1:3000`

Tear down cloud resources:

```bash
./scripts/down.sh
```

## Limitations

- Single-node GKE dev cluster
- Limited concurrent match capacity
- Basic Admin View password protection only; no OAuth, roles, or production identity provider yet
- No public domain or Ingress yet
- In-cluster PostgreSQL is dev-grade
- Multi-region capacity is operated from one central control plane and still uses dev-grade static regional service-account kubeconfig credentials
- Minimal alerts/runbooks
- Capacity depends on Fleet/FleetAutoscaler and Kubernetes node resources

## Future Work

- Stronger admin authentication and role separation
- Public Ingress/domain
- Managed PostgreSQL or backups
- Additional tournament formats
- Matchmaking or match request queue
- Improved Fleet autoscaling and scheduling
- Alerts for allocation, RCON, verification, and capacity issues
- Multi-node regional pools and stronger cross-cluster identity
- CI/CD deployment automation from GitHub to GKE

## Repository Map

- `infra/`: Terraform for GCP/GKE
- `server/`: Xonotic server image and runtime config
- `allocator-backend/`: Flask allocator and platform API
- `allocator-frontend/`: React admin/player UI
- `platform/agones/`: Agones Fleet/FleetAutoscaler manifests
- `platform/postgres/`: PostgreSQL dev manifests
- `platform/observability/`: Prometheus, Loki, Alloy, and Grafana manifests
- `scripts/`: local bring-up, tear-down, and verification helpers
