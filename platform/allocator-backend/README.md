# Allocator Backend Phase

This phase adds the first backend service that allocates Xonotic game servers programmatically from inside the cluster.

It now also includes the first in-memory Match Room layer for the admin workflow. Match Rooms are the operator-facing objects; allocated Agones `GameServer` instances are the infrastructure assigned to those rooms.

## Why This Backend Uses The Kubernetes API In-Cluster

This backend runs as a Kubernetes Pod and only needs to create and read `GameServerAllocation` resources in the local cluster.

For this phase, using the Kubernetes API directly is the simplest and most practical option:

- no external Agones Allocator Service required
- no extra network exposure for allocation traffic
- no extra auth layer beyond Kubernetes ServiceAccount RBAC
- keeps the implementation tiny and easy to review

## API

- `GET /healthz`: simple health check
- `GET /fleet-status`: current Fleet summary for the operator UI
- `GET /gameservers`: current `GameServer` list for the operator UI
- `POST /matches`: create an in-memory Match Room
- `GET /matches`: list in-memory Match Rooms
- `GET /matches/<match_id>`: inspect one Match Room
- `POST /matches/<match_id>/allocate`: allocate one Agones `GameServer` for a Match Room
- `POST /allocate`: creates a `GameServerAllocation`, waits for the result, and returns the allocated address and port

`POST /allocate` remains available for direct/manual debugging. Normal admin flow should use Match Rooms.

Match Room state is intentionally process-local memory. It is lost when the backend Pod restarts. That keeps this phase small while still moving the project toward a tournament admin tool.

Current real fields:

- `match_id`
- `name`
- `status`
- `created_at`
- `allocated_at`
- `max_players`
- `game_mode`
- allocated server address, port, GameServer name, and allocation request name

Current placeholder fields:

- `current_players`: `null` until server telemetry exists
- `map`: `null` until allocation/server metadata or runtime reporting exists

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
4. returns the allocated `address` and `port`

## Files

- `manifests/namespace.yaml`: namespace for the backend service
- `manifests/rbac.yaml`: `ServiceAccount`, `Role`, and `RoleBinding`
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

Apply the namespace and RBAC:

```bash
kubectl apply -f platform/allocator-backend/manifests/namespace.yaml
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
curl -fsS -X POST http://127.0.0.1:18080/allocate
```

Create and allocate a Match Room:

```bash
curl -fsS -X POST http://127.0.0.1:18080/matches \
  -H "content-type: application/json" \
  -d '{"name":"Quarterfinal 1","max_players":8,"game_mode":"dm"}'

curl -fsS http://127.0.0.1:18080/matches

curl -fsS http://127.0.0.1:18080/matches/<match_id>

curl -fsS -X POST http://127.0.0.1:18080/matches/<match_id>/allocate
```

Inspect the allocated server endpoint:

```bash
curl -fsS -X POST http://127.0.0.1:18080/allocate
```
