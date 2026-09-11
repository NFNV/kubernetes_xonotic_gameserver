# Allocator Backend

The Flask allocator is the centralized control-plane API for regional Xonotic GameServer allocation and tournament operations.

Persisted tournament matches are the normal operator workflow. The older in-memory Match Room layer remains available under Advanced / Debug for lower-level allocation and RCON testing; allocated Agones `GameServer` instances are the infrastructure backing both flows.

Match Rooms now store the requested map and game mode before allocation. Because the Fleet uses already-running standby servers, allocation does not create a fresh preconfigured server. Instead, the backend allocates a warm server, applies the requested map/mode through whitelisted RCON, verifies the result with `getstatus`, and only then marks the room joinable. Max-player control remains deferred.

PostgreSQL persists tournaments, teams, rounds, matches, results, bracket advancement, finalization, and server assignment history. Lower-level Match Rooms and live server telemetry remain in-memory/runtime-owned.

## Why This Backend Uses Regional Kubernetes API Clients

This backend runs as a Kubernetes Pod in South America and only needs to create/read `GameServerAllocation` resources, inspect Fleet/GameServer capacity, and delete allocated `GameServer` resources in each configured regional cluster.

For this phase, using the Kubernetes API directly is the simplest and most practical option:

- no external Agones Allocator Service required
- no extra network exposure for allocation traffic
- namespaced regional ServiceAccount RBAC plus basic password auth for mutating admin API calls
- keeps the implementation tiny and easy to review

## API

- `GET /healthz`: simple health check
- `GET /version`: deployed release identity
- `GET /metrics`: Prometheus application metrics
- `GET /game-config/options`: verified map/mode combinations
- `GET /server-pools`: configured regional pools
- `GET /server-pools/capacity`: live Fleet capacity by regional pool
- `GET /admin/session`: check Admin View session state
- `POST /admin/login`: create an admin session from the configured password hash
- `POST /admin/logout`: clear the admin session
- `GET /fleet-status`: current Fleet summary for the operator UI
- `GET /gameservers`: current `GameServer` list for the operator UI
- `POST /tournaments`: create a persisted tournament
- `GET /tournaments`: list persisted tournaments
- `GET /tournaments/<tournament_id>`: inspect one persisted tournament
- `GET /tournaments/<tournament_id>/summary`: get backend-owned winner/progress/final-match summary
- `POST /tournaments/<tournament_id>/finalize`: mark a tournament `completed`, store `winner_team_id`, and set `completed_at` once the final match has a recorded winner
- `POST /tournaments/<tournament_id>/teams`: create a team
- `GET /tournaments/<tournament_id>/teams`: list teams
- `POST /tournaments/<tournament_id>/rounds`: create a round
- `GET /tournaments/<tournament_id>/rounds`: list rounds
- `POST /tournaments/<tournament_id>/bracket/generate`: generate a 2-, 4-, or 8-team single-elimination bracket from seeded teams
- `POST /tournaments/<tournament_id>/matches`: create a tournament match record
- `GET /tournaments/<tournament_id>/matches`: list tournament match records
- `POST /tournaments/<tournament_id>/matches/<match_id>/allocate-server`: allocate and verify a regional server for a persisted match
- `POST /tournaments/<tournament_id>/matches/<match_id>/result`: record a result, advance the bracket winner, and release the match server
- `POST /tournaments/<tournament_id>/matches/<match_id>/release-server`: manually release one persisted match server
- `POST /tournaments/<tournament_id>/server-assignments/release-all`: release all active assignments for a tournament
- `POST /matches`: create an in-memory Match Room
- `GET /matches`: list in-memory Match Rooms
- `GET /matches/<match_id>`: inspect one Match Room
- `PATCH /matches/<match_id>`: edit requested map/mode before allocation
- `POST /matches/<match_id>/allocate`: allocate one Agones `GameServer`, apply requested map/mode, and expose it only after verification
- `POST /matches/<match_id>/release`: end a Match Room and delete the allocated Agones `GameServer`
- `POST /matches/<match_id>/rcon-smoke-test`: backend-only RCON verification for an allocated Match Room
- `POST /matches/<match_id>/admin/broadcast`: broadcast a validated message to an allocated Match Room
- `POST /matches/<match_id>/admin/change-map`: change an allocated Match Room to an allowlisted map
- `POST /allocated-servers/<gameserver_name>/terminate`: terminate an allocated GameServer directly after validating it is `Allocated`
- `POST /allocate`: creates a `GameServerAllocation`, waits for the result, and returns the allocated address and port

`POST /allocate` and Match Rooms remain available for direct/manual debugging. Normal admin flow should use persisted tournament matches.

Match Room state is intentionally process-local memory. It is lost when the backend Pod restarts. That keeps this phase small while still moving the project toward a tournament admin tool.

Tournament state is PostgreSQL-backed. Single-elimination bracket generation, result recording, winner advancement, backend-owned tournament summaries, explicit finalization, persisted server assignments, and basic admin password protection are implemented; other tournament formats and persisted Match Rooms are intentionally deferred.

For allocated Match Rooms, the backend queries the assigned Xonotic server with UDP `getstatus` and briefly caches the result. This provides live map, game mode, player count, player names, scores, ping, and team scores when available. It is read-only and does not use RCON.

Current real fields:

- `match_id`
- `name`
- `status`
- `created_at`
- `allocated_at`
- `released_at`
- `game_mode`
- `requested_map`
- `requested_game_mode`
- `joinable`
- `allocation_config_result`
- allocated server address, port, GameServer name, and allocation request name
- best-effort live status from Xonotic `getstatus`

Current temporary limitations:

- live status is cached briefly and may be stale for a few seconds
- status is unavailable until a room has an allocated server
- map/mode configuration depends on Xonotic accepting the whitelisted RCON commands and reporting the expected values through `getstatus`
- max-player control is deferred pending a safe verified command path
- RCON controls are whitelisted only; there is no raw command endpoint and the frontend never receives the RCON password
- Match Room and live status state are not persisted across backend restarts

Expected JSON response:

```json
{
  "allocation_request_name": null,
  "allocated_game_server_name": "xonotic-fleet-abcde-fghij",
  "address": "34.176.10.20",
  "port": 7003
}
```

## Allocation Flow

The backend:

1. creates a `GameServerAllocation` in namespace `xonotic-agones`
2. targets `xonotic-fleet`
3. reads back the allocation result
4. sends whitelisted RCON commands for requested game mode and map
5. verifies the live map/mode with `getstatus`
6. returns the allocated `address` and `port` only as a joinable Match Room when verification succeeds

If verification fails, the Match Room is marked `allocated_needs_attention`, `joinable` remains `false`, and the endpoint should not be treated as ready for players. The room can still be released, which deletes the allocated Agones `GameServer`.

## Files

- `manifests/namespace.yaml`: namespace for the backend service
- `manifests/rbac.yaml`: `ServiceAccount`, `Role`, and `RoleBinding`; includes namespaced `GameServer` delete so release can remove an allocated server
- `manifests/deployment.yaml`: backend Deployment
- `manifests/service.yaml`: in-cluster ClusterIP Service

## Image Naming Convention

The backend image is separate from the game server image:

- `ghcr.io/nfnv/xonotic-allocator-backend`

Published releases use coordinated `sha-<40-character-commit>` tags. A `master` convenience tag may exist, but Kubernetes CD deploys only immutable SHA tags.

## Build And Push The Image

Repository-native path:

- merge through `master` CI and let `.github/workflows/publish-images.yml` publish the coordinated release, or dispatch that workflow manually with a full Git SHA

Direct local path:

```bash
export ALLOCATOR_BACKEND_IMAGE="ghcr.io/nfnv/xonotic-allocator-backend:allocator-backend"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
docker buildx build --platform linux/amd64 -t "$ALLOCATOR_BACKEND_IMAGE" --push ./allocator-backend
```

## Deploy

For the current repo phase, `./scripts/up.sh` already deploys these manifests after Agones, the `Fleet`, and the `FleetAutoscaler` are healthy.

Manual deployment remains:

Refresh all three GKE contexts and build the backend-only kubeconfig first:

```bash
gcloud container clusters get-credentials xonotic-mvp \
  --zone southamerica-west1-a --project "${GCP_PROJECT_ID}"
gcloud container clusters get-credentials xonotic-eu \
  --zone europe-west1-b --project "${GCP_PROJECT_ID}"
gcloud container clusters get-credentials xonotic-na \
  --zone us-central1-a --project "${GCP_PROJECT_ID}"
./scripts/build-multicluster-kubeconfig.sh
```

Apply the namespace, PostgreSQL Secret, PostgreSQL manifests, and RBAC:

```bash
kubectl apply -f platform/allocator-backend/manifests/namespace.yaml
./scripts/apply-multicluster-kubeconfig-secret.sh
eval "$(scripts/generate-admin-auth.sh --username admin --password admin)"
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: xonotic-postgres
  namespace: xonotic-allocator-backend
type: Opaque
stringData:
  POSTGRES_DB: ${XONOTIC_POSTGRES_DB}
  POSTGRES_USER: ${XONOTIC_POSTGRES_USER}
  POSTGRES_PASSWORD: ${XONOTIC_POSTGRES_PASSWORD}
EOF
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: xonotic-admin-auth
  namespace: xonotic-allocator-backend
type: Opaque
stringData:
  ADMIN_USERNAME: ${ADMIN_USERNAME:-admin}
  ADMIN_PASSWORD_HASH: ${ADMIN_PASSWORD_HASH}
  ADMIN_SESSION_SECRET: ${ADMIN_SESSION_SECRET}
EOF
kubectl apply -f platform/postgres/manifests/pvc.yaml
kubectl apply -f platform/postgres/manifests/service.yaml
kubectl apply -f platform/postgres/manifests/deployment.yaml
kubectl rollout status deployment/xonotic-postgres -n xonotic-allocator-backend
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: xonotic-rcon
  namespace: xonotic-allocator-backend
type: Opaque
stringData:
  XONOTIC_RCON_PASSWORD: ${XONOTIC_RCON_PASSWORD}
EOF
kubectl apply -f platform/allocator-backend/manifests/rbac.yaml
```

Deploy the backend:

```bash
kubectl apply -f platform/allocator-backend/manifests/deployment.yaml
kubectl apply -f platform/allocator-backend/manifests/service.yaml
```

Verify it:

```bash
kubectl get pods -n xonotic-allocator-backend
kubectl get service -n xonotic-allocator-backend
kubectl get deployment xonotic-allocator-backend -n xonotic-allocator-backend -o jsonpath='{.spec.template.spec.containers[*].name}{"\n"}'
kubectl get secret xonotic-admin-auth -n xonotic-allocator-backend -o go-template='{{range $k, $_ := .data}}{{println $k}}{{end}}'
kubectl logs deployment/xonotic-allocator-backend -n xonotic-allocator-backend --tail=100
```

## Test With Port Forward And curl

Port forward the service:

```bash
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-backend 18082:8080
```

Check the public health, release, and capacity endpoints:

```bash
curl -fsS http://127.0.0.1:18082/healthz | jq
curl -fsS http://127.0.0.1:18082/version | jq
curl -fsS http://127.0.0.1:18082/server-pools/capacity | jq
```

Mutating endpoints require an authenticated admin session:

```bash
ADMIN_COOKIE="$(mktemp)"
curl -fsS -c "${ADMIN_COOKIE}" -X POST http://127.0.0.1:18082/admin/login \
  -H "content-type: application/json" \
  -d '{"username":"admin","password":"<admin-password>"}' | jq
```

Create and allocate a Match Room:

```bash
MATCH_ID="$(curl -fsS -b "${ADMIN_COOKIE}" -X POST http://127.0.0.1:18082/matches \
  -H "content-type: application/json" \
  -d '{"name":"Allocator smoke test","requested_map":"xoylent","requested_game_mode":"dm"}' | jq -r .match_id)"
curl -fsS -b "${ADMIN_COOKIE}" -X POST "http://127.0.0.1:18082/matches/${MATCH_ID}/allocate" \
  -H "content-type: application/json" \
  -d '{}' | jq
```

Release the disposable server when finished:

```bash
curl -fsS -b "${ADMIN_COOKIE}" -X POST "http://127.0.0.1:18082/matches/${MATCH_ID}/release" \
  -H "content-type: application/json" -d '{}' | jq
```

Use the [operations runbook](../../docs/operations.md) for broader health checks and the [RCON guide](../../docs/rcon-admin-controls.md) for privileged command tests.
