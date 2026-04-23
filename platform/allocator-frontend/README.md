# Allocator Frontend Phase

This phase adds a small React admin dashboard for operators.

It is not a public user-facing site. It is a simple control panel for:

- checking allocator backend health
- inspecting Fleet capacity
- reviewing the current `GameServer` list
- allocating one server and seeing the latest result

## How It Works

The frontend is a static React app served by nginx inside the cluster.

The nginx container proxies `/api` requests to the existing allocator backend service, so the browser only needs to talk to one frontend endpoint.

Backend endpoints used:

- `GET /healthz`
- `GET /fleet-status`
- `GET /gameservers`
- `POST /allocate`

## Image Naming Convention

- `ghcr.io/nfnv/xonotic-allocator-frontend`

Tags:

- stable tag: `allocator-frontend`
- trace tag: `sha-<12-char-commit>`

## Build And Push

Repository-native path:

- push changes under `allocator-frontend/` to `master`, or run the `publish-allocator-frontend-image.yml` workflow manually in GitHub Actions

Direct local path:

```bash
export ALLOCATOR_FRONTEND_IMAGE="ghcr.io/nfnv/xonotic-allocator-frontend:allocator-frontend"
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
docker buildx build --platform linux/amd64 -t "$ALLOCATOR_FRONTEND_IMAGE" --push ./allocator-frontend
```

## Deploy

The frontend runs in the existing `xonotic-allocator-backend` namespace to keep service discovery simple for this phase.

For the current repo phase, `./scripts/up.sh` already deploys the allocator frontend after the backend rollout succeeds.

```bash
kubectl apply -f platform/allocator-frontend/manifests/deployment.yaml
kubectl apply -f platform/allocator-frontend/manifests/service.yaml
kubectl rollout status deployment/xonotic-allocator-frontend -n xonotic-allocator-backend
```

## Access

Use port-forward for this MVP admin path:

```bash
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-frontend 18081:8080
```

Then open:

```text
http://127.0.0.1:18081
```
