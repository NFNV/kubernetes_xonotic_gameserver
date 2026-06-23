# Allocator Backend Phase

This phase adds the first backend service that allocates Xonotic game servers programmatically from inside the cluster.

It now also includes the first in-memory Match Room layer for the admin workflow. Match Rooms are the operator-facing objects; allocated Agones `GameServer` instances are the infrastructure assigned to those rooms.

Match Rooms now store the requested map and game mode before allocation. Because the Fleet uses already-running standby servers, allocation does not create a fresh preconfigured server. Instead, the backend allocates a warm server, applies the requested map/mode through whitelisted RCON, verifies the result with `getstatus`, and only then marks the room joinable. Max-player control remains deferred.

This phase also adds PostgreSQL-backed tournament CRUD as a foundation for the future tournament admin tool. Tournament records, teams, rounds, and tournament matches are persisted; Match Rooms and live server telemetry remain in-memory/runtime-owned for now.

## Why This Backend Uses Regional Kubernetes API Clients

This backend runs as a Kubernetes Pod in South America and only needs to create/read `GameServerAllocation` resources, inspect Fleet/GameServer capacity, and delete allocated `GameServer` resources in each configured regional cluster.

For this phase, using the Kubernetes API directly is the simplest and most practical option:

- no external Agones Allocator Service required
- no extra network exposure for allocation traffic
- namespaced regional ServiceAccount RBAC plus basic password auth for mutating admin API calls
- keeps the implementation tiny and easy to review

## API

- `GET /healthz`: simple health check
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

`POST /allocate` remains available for direct/manual debugging. Normal admin flow should use Match Rooms.

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

Tags:

- stable tag: `allocator-backend`
- trace tag: `sha-<12-char-commit>`

## Build And Push The Image

Repository-native path:

- push changes under `allocator-backend/` to `master`, or run the `publish-allocator-backend-image.yml` workflow manually in GitHub Actions

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
kubectl create secret generic xonotic-multicluster-kubeconfig \
  -n xonotic-allocator-backend \
  --from-file=config="${XONOTIC_MULTICLUSTER_KUBECONFIG:-scripts/.generated/xonotic-multicluster.kubeconfig}" \
  --from-literal=XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT="${XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT}" \
  --from-literal=XONOTIC_EUROPE_KUBE_CONTEXT="${XONOTIC_EUROPE_KUBE_CONTEXT}" \
  --from-literal=XONOTIC_NORTH_AMERICA_KUBE_CONTEXT="${XONOTIC_NORTH_AMERICA_KUBE_CONTEXT}" \
  --dry-run=client -o yaml | kubectl apply -f -
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
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-backend 18080:8080
```

Then call the API:

```bash
curl -fsS http://127.0.0.1:18080/healthz
ADMIN_COOKIE="$(mktemp)"
curl -fsS -c "${ADMIN_COOKIE}" -X POST http://127.0.0.1:18080/admin/login \
  -H "content-type: application/json" \
  -d '{"username":"admin","password":"admin"}'
curl -fsS -b "${ADMIN_COOKIE}" -X POST http://127.0.0.1:18080/allocate
```

Create persisted tournament records:

```bash
TOURNAMENT_ID="$(curl -fsS -b "${ADMIN_COOKIE}" -X POST http://127.0.0.1:18080/tournaments \
  -H "content-type: application/json" \
  -d '{"name":"Spring Arena Cup"}' | jq -r .id)"

TEAM_A_ID="$(curl -fsS -b "${ADMIN_COOKIE}" -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/teams" \
  -H "content-type: application/json" \
  -d '{"name":"Blue Rockets","tag":"BLUE","seed":1}' | jq -r .id)"

TEAM_B_ID="$(curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/teams" \
  -H "content-type: application/json" \
  -d '{"name":"Orange Railers","tag":"ORNG","seed":2}' | jq -r .id)"

ROUND_ID="$(curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/rounds" \
  -H "content-type: application/json" \
  -d '{"name":"Round 1","round_order":1}' | jq -r .id)"

curl -fsS -X POST "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches" \
  -H "content-type: application/json" \
  -d "{\"round_id\":\"${ROUND_ID}\",\"team_a_id\":\"${TEAM_A_ID}\",\"team_b_id\":\"${TEAM_B_ID}\",\"requested_map\":\"stormkeep\",\"requested_game_mode\":\"dm\"}" | jq

curl -fsS "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/matches" | jq
curl -fsS "http://127.0.0.1:18080/tournaments/${TOURNAMENT_ID}/summary" | jq
```

Create and allocate a Match Room:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches \
  -H "content-type: application/json" \
  -d '{"name":"Quarterfinal 1","requested_map":"stormkeep","requested_game_mode":"dm"}'

curl -fsS http://127.0.0.1:18080/matches

curl -fsS http://127.0.0.1:18080/matches/<match_id>

curl -fsS -X PATCH http://127.0.0.1:18080/matches/<match_id> \
  -H "content-type: application/json" \
  -d '{"requested_map":"xoylent","requested_game_mode":"dm"}'

curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/allocate \
  -H "content-type: application/json" \
  -d '{"requested_map":"stormkeep","requested_game_mode":"dm"}'

curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/rcon-smoke-test

curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/admin/broadcast \
  -H "content-type: application/json" \
  -d '{"message":"Match starts in 2 minutes"}'

curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/admin/change-map \
  -H "content-type: application/json" \
  -d '{"map":"stormkeep"}'

curl -fsS -X POST http://127.0.0.1:18080/allocated-servers/<gameserver_name>/terminate

curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/release
```

Inspect the allocated server endpoint:

```bash
curl -fsS -X POST http://127.0.0.1:18080/allocate
```
