# Observability MVP

This directory contains a lightweight Prometheus and Grafana stack for the small single-node GKE dev cluster.

It is intentionally not a production monitoring stack. It does not install Alertmanager, Loki, Tempo, an operator, custom resource definitions, or persistent monitoring storage. Prometheus scrapes only the allocator backend `/metrics` endpoint, keeps a short retention window, and stores data in an ephemeral `emptyDir`.

## Deploy

First rebuild and redeploy the allocator backend image so `/metrics` is available:

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/nfnv/xonotic-allocator-backend:allocator-backend \
  --push allocator-backend

kubectl rollout restart deployment/xonotic-allocator-backend -n xonotic-allocator-backend
kubectl rollout status deployment/xonotic-allocator-backend -n xonotic-allocator-backend
```

Then apply the observability manifests:

```bash
kubectl apply -k platform/observability
kubectl rollout status deployment/xonotic-prometheus -n xonotic-observability
kubectl rollout status deployment/xonotic-grafana -n xonotic-observability
```

## Access

Grafana:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-grafana 3000:3000
```

Open `http://127.0.0.1:3000`.

Prometheus:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-prometheus 9090:9090
```

Open `http://127.0.0.1:9090`.

## Test

Verify the backend exposes metrics directly:

```bash
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-backend 18082:8080
curl -fsS http://127.0.0.1:18082/metrics | grep allocator_
```

Verify Prometheus sees the backend target:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-prometheus 9090:9090
curl -fsS http://127.0.0.1:9090/api/v1/targets
```

The Grafana dashboard is provisioned into the `Xonotic` folder as `Xonotic Allocator MVP`.

## Metrics

- `allocator_backend_http_requests_total`: count of backend HTTP requests by method, endpoint, and status.
- `allocator_backend_http_request_duration_seconds`: backend HTTP request latency histogram by method and endpoint.
- `allocator_allocation_attempts_total`: count of Agones allocation attempts.
- `allocator_allocation_successes_total`: count of successful Agones allocations.
- `allocator_allocation_failures_total`: count of failed Agones allocations by failure reason.
- `allocator_active_match_server_assignments`: gauge of persisted tournament match server assignments with `status = active`.
- `allocator_rcon_command_attempts_total`: count of RCON command attempts by command name.
- `allocator_rcon_command_failures_total`: count of failed RCON command attempts by command name and failure reason.
- `allocator_map_mode_verification_successes_total`: count of map/mode verification successes by requested mode and map.
- `allocator_map_mode_verification_failures_total`: count of map/mode verification failures by requested mode, map, and reason.

## Regional Capacity Dashboard Note

The current Grafana dashboard already shows allocation failures and active match assignments. Full Ready/Allocated GameServer panels by server pool or region are intentionally deferred because the current Prometheus metrics do not yet label capacity by `server_pool_id` or `region`. Do not fake those series in Grafana.

TODO: add backend Prometheus gauges for server-pool capacity with pool/region labels, such as Ready, Allocated, Desired, and Current replicas, then add regional panels to the dashboard.

## Resource Impact

Prometheus requests `25m` CPU and `128Mi` memory, limits at `200m` CPU and `256Mi` memory, keeps only `6h` of data, and caps TSDB size at `256MB` on a `512Mi` ephemeral volume.

Grafana requests `25m` CPU and `128Mi` memory, limits at `200m` CPU and `256Mi` memory, and uses ConfigMap-provisioned dashboards/datasources instead of extra storage.

Together the stack requests `50m` CPU and `256Mi` memory, with maximum limits of `400m` CPU and `512Mi` memory.
