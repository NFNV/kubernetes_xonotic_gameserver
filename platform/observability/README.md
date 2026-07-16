# Observability

This directory contains the lightweight metrics and logging stack for the primary South America dev/control-plane cluster.

It is intentionally not a production monitoring suite. It does not install Prometheus Operator, Alertmanager, Tempo, custom resource definitions, or persistent monitoring storage. Prometheus and Loki use bounded ephemeral volumes with short retention windows, and all services remain `ClusterIP` only.

## Components

- Prometheus (`xonotic-prometheus`) scrapes metrics and stores short-lived time series for debugging and day-to-day operations.
- Loki (`xonotic-loki`) stores compressed Kubernetes pod logs for up to 24 hours on a bounded `emptyDir` volume.
- Grafana Alloy (`xonotic-alloy`) runs as a DaemonSet, discovers pods on its local node through the Kubernetes API, and forwards their logs to Loki without privileged host filesystem mounts.
- Grafana (`xonotic-grafana`) reads Prometheus and Loki and serves provisioned metrics and logs dashboards through local port-forwarding.
- kube-state-metrics (`xonotic-kube-state-metrics`) exposes Kubernetes object and state metrics such as pod phases, restarts, node conditions, Deployment state, and resource requests.
- node-exporter (`xonotic-node-exporter`) runs once per node and exposes node CPU, memory, disk, filesystem, and network metrics.
- Prometheus also scrapes kubelet and cAdvisor metrics through the Kubernetes API server proxy for pod/container CPU and memory usage.

In short: Prometheus stores metrics, Loki stores logs, Alloy collects logs, and Grafana visualizes both.

## Deploy

The normal primary-environment entrypoint deploys this stack automatically after the allocator backend and frontend are Ready:

```bash
./scripts/up.sh
```

Observability is default-on but non-fatal. If Prometheus or Grafana cannot roll out, `up.sh` prints a warning and leaves the primary backend/frontend environment usable.

To manually reconcile only the observability stack:

```bash
kubectl apply -k platform/observability
kubectl rollout restart deployment/xonotic-prometheus -n xonotic-observability
kubectl rollout restart deployment/xonotic-loki -n xonotic-observability
kubectl rollout restart daemonset/xonotic-alloy -n xonotic-observability
kubectl rollout restart deployment/xonotic-grafana -n xonotic-observability
kubectl rollout status deployment/xonotic-prometheus -n xonotic-observability
kubectl rollout status deployment/xonotic-kube-state-metrics -n xonotic-observability
kubectl rollout status daemonset/xonotic-node-exporter -n xonotic-observability
kubectl rollout status deployment/xonotic-loki -n xonotic-observability
kubectl rollout status daemonset/xonotic-alloy -n xonotic-observability
kubectl rollout status deployment/xonotic-grafana -n xonotic-observability
```

If backend metrics code changed, first rebuild and redeploy the allocator backend image so `/metrics` is available:

```bash
docker buildx build --platform linux/amd64 \
  -t ghcr.io/nfnv/xonotic-allocator-backend:allocator-backend \
  --push allocator-backend

kubectl rollout restart deployment/xonotic-allocator-backend -n xonotic-allocator-backend
kubectl rollout status deployment/xonotic-allocator-backend -n xonotic-allocator-backend
kubectl apply -k platform/observability
```

## Access

Grafana:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-grafana 3000:3000
```

Open `http://127.0.0.1:3000`. Anonymous Viewer access is enabled for local port-forwarded use, and the default admin/admin credentials are only reachable through the internal `ClusterIP` service plus port-forward.

Prometheus:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-prometheus 9090:9090
```

Open `http://127.0.0.1:9090`.

Loki API, for readiness and direct LogQL verification:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-loki 3100:3100
```

Check `http://127.0.0.1:3100/ready`. Normal log exploration should happen through Grafana rather than the Loki API.

## Dashboards

Grafana provisions dashboards into the `Xonotic` folder:

- `Xonotic Cluster Overview`: cluster node count, running pods, node pressure, node CPU, node memory, root disk utilization, node network throughput, top pod CPU, top pod memory, and pod restarts.
- `Xonotic Allocator Operations`: backend HTTP request rate, backend request latency, allocation successes/failures, active match assignments, Xonotic namespace pod CPU/memory, pod restarts, RCON failures, map/mode verification failures, and an explicit Agones capacity TODO panel.
- `Xonotic Platform Logs`: allocator backend logs, allocation failure filtering, RCON/`getstatus` errors, and Agones/Xonotic GameServer logs.

## Metrics

Allocator backend metrics:

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

Kubernetes infrastructure metrics:

- node CPU, memory, disk, filesystem, and network metrics from node-exporter.
- pod/container CPU and memory metrics from kubelet/cAdvisor.
- pod restarts, pod phases, node conditions, Deployment state, and resource-request metadata from kube-state-metrics.

Kubernetes log labels:

- `namespace`, `pod`, and `container` identify the workload source.
- `app` is copied from the pod's `app` or `app.kubernetes.io/name` label.
- `cluster="xonotic-mvp"` and `region="south-america"` identify this primary deployment.
- `server_pool_id` is retained when a workload provides that pod label; it is not invented for unlabeled control-plane pods.

## Useful PromQL

Node CPU utilization:

```promql
100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])))
```

Node memory utilization:

```promql
100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))
```

Node pressure:

```promql
sum(kube_node_status_condition{condition=~"MemoryPressure|DiskPressure|PIDPressure",status="true"})
```

Pod CPU usage:

```promql
sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{container!="",container!="POD",pod!=""}[5m]))
```

Pod memory usage:

```promql
sum by (namespace, pod) (container_memory_working_set_bytes{container!="",container!="POD",pod!=""})
```

Pod restarts in the last hour:

```promql
sum by (namespace, pod, container) (increase(kube_pod_container_status_restarts_total[1h]))
```

Backend request rate:

```promql
sum by (endpoint, status) (rate(allocator_backend_http_requests_total[5m]))
```

Backend p95 latency:

```promql
histogram_quantile(0.95, sum by (le, endpoint) (rate(allocator_backend_http_request_duration_seconds_bucket[5m])))
```

Allocation successes and failures:

```promql
sum(rate(allocator_allocation_successes_total[5m]))
sum by (reason) (rate(allocator_allocation_failures_total[5m]))
```

Active match assignments:

```promql
allocator_active_match_server_assignments
```

## Useful LogQL

Allocator backend logs:

```logql
{namespace="xonotic-allocator-backend", app="xonotic-allocator-backend"}
```

Allocation failures and capacity errors:

```logql
{namespace="xonotic-allocator-backend", app="xonotic-allocator-backend"}
  |~ "(?i)(allocation.*(fail|error)|no.ready.*server|no_ready_servers)"
```

RCON and `getstatus` failures:

```logql
{namespace="xonotic-allocator-backend", app="xonotic-allocator-backend"}
  |~ "(?i)((rcon|getstatus).*(fail|error|timeout)|(fail|error|timeout).*(rcon|getstatus))"
```

Xonotic GameServer logs:

```logql
{namespace="xonotic-agones", container="server"}
```

All logs from the primary cluster for a specific pod:

```logql
{cluster="xonotic-mvp", pod="POD_NAME"}
```

## Test

Check workloads and Pods:

```bash
kubectl get deploy,daemonset,pod,svc -n xonotic-observability
```

Check services remain internal:

```bash
kubectl get svc -n xonotic-observability
```

Port-forward Grafana:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-grafana 3000:3000
```

Port-forward Prometheus:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-prometheus 9090:9090
```

Verify the backend exposes metrics directly:

```bash
kubectl port-forward -n xonotic-allocator-backend service/xonotic-allocator-backend 18082:8080
curl -fsS http://127.0.0.1:18082/metrics | rg 'allocator_'
```

Verify Prometheus targets:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-prometheus 9090:9090
curl -fsS http://127.0.0.1:9090/api/v1/targets | rg 'allocator-backend|kube-state-metrics|node-exporter|kubernetes-kubelet|kubernetes-cadvisor'
```

Verify Loki and Alloy:

```bash
kubectl rollout status deployment/xonotic-loki -n xonotic-observability
kubectl rollout status daemonset/xonotic-alloy -n xonotic-observability
kubectl logs -n xonotic-observability daemonset/xonotic-alloy --tail=100
kubectl port-forward -n xonotic-observability service/xonotic-loki 3100:3100
curl -fsS http://127.0.0.1:3100/ready
curl -G -fsS http://127.0.0.1:3100/loki/api/v1/query_range \
  --data-urlencode 'query={namespace="xonotic-allocator-backend"}' \
  --data-urlencode 'limit=20'
```

Verify Grafana provisioned the Loki data source:

```bash
kubectl port-forward -n xonotic-observability service/xonotic-grafana 3000:3000
curl -fsS -u admin:admin http://127.0.0.1:3000/api/datasources/uid/loki
```

Query Prometheus from the API:

```bash
curl -G -fsS http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=up'
curl -G -fsS http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m])))'
curl -G -fsS http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=sum by (namespace, pod) (container_memory_working_set_bytes{container!="",container!="POD",pod!=""})'
curl -G -fsS http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=sum by (namespace, pod, container) (increase(kube_pod_container_status_restarts_total[1h]))'
curl -G -fsS http://127.0.0.1:9090/api/v1/query --data-urlencode 'query=sum by (endpoint, status) (rate(allocator_backend_http_requests_total[5m]))'
```

## Agones Capacity Limitation

The dashboards do not fake GameServer Ready/Allocated or Fleet capacity metrics. The current lightweight stack does not yet scrape Agones controller metrics or configure kube-state-metrics custom-resource-state for Agones CRDs.

TODO: add a real Agones metric source, either by scraping Agones controller metrics if they expose Fleet/GameServer capacity series, or by configuring kube-state-metrics custom resource state for `agones.dev` Fleet and GameServer CRDs. Then add capacity panels grouped by namespace, fleet, pool, and region.

Backend-derived allocation metrics and Kubernetes pod metrics are still available today.

## Regional / Multicluster Limitation

This stack observes metrics and logs from the primary South America control-plane cluster only. Europe and North America currently run game-server-plane resources without Alloy, Loki, or regional Prometheus deployments, so their GameServer logs are not sent to the primary Loki instance.

Useful multicluster observability would require one of these follow-up designs:

- one Prometheus and Alloy collector per regional cluster, with controlled forwarding/federation to a central observability plane, or
- central collectors scraping remote regional clusters with explicit credentials, API access, network reachability, and careful query/ingestion limits.

Do not bolt full federation into the small dev cluster until there is a clear capacity and access plan.

## Resource Impact

Prometheus requests `25m` CPU and `128Mi` memory, limits at `200m` CPU and `256Mi` memory, keeps only `6h` of data, and caps TSDB size at `256MB` on a `512Mi` ephemeral volume.

Grafana requests `25m` CPU and `128Mi` memory, limits at `200m` CPU and `256Mi` memory, and uses ConfigMap-provisioned dashboards/datasources instead of extra storage.

kube-state-metrics requests `20m` CPU and `64Mi` memory, limits at `100m` CPU and `128Mi` memory.

node-exporter runs once per node and requests `10m` CPU and `32Mi` memory per node, limits at `100m` CPU and `64Mi` memory per node.

Loki requests `25m` CPU and `96Mi` memory, limits at `200m` CPU and `256Mi` memory, retains logs for 24 hours, and uses a `1Gi` bounded ephemeral volume. Logs are lost when the Loki Pod is recreated or the node disappears; this is intentional for the dev cluster.

Alloy runs once per node and requests `20m` CPU and `64Mi` memory per node, limits at `100m` CPU and `128Mi` memory. It tails only pods on its own node and uses the Kubernetes API instead of privileged host mounts.

On a one-node dev cluster, the complete observability stack requests about `125m` CPU and `512Mi` memory total, with limits around `900m` CPU and `1088Mi` memory. Each additional node adds one node-exporter Pod plus one Alloy Pod, requesting another `30m` CPU and `96Mi` memory. This is still appropriate for development, but operators should watch node memory and pod scheduling before increasing concurrent GameServer capacity.
