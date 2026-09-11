# Observability

This directory contains the lightweight metrics and logging stack for the primary South America dev/control-plane cluster.

It is intentionally not a production monitoring suite. It does not install Prometheus Operator, Alertmanager, Tempo, additional custom resource definitions, or persistent metrics/log storage. Prometheus and Loki use bounded ephemeral volumes with short retention windows, while Grafana keeps its small SQLite configuration database on a 2Gi persistent volume. All services remain `ClusterIP` only.

## Components

- Prometheus (`xonotic-prometheus`) scrapes metrics and stores short-lived time series for debugging and day-to-day operations.
- Loki (`xonotic-loki`) stores compressed Kubernetes pod logs for up to 24 hours on a bounded `emptyDir` volume.
- Grafana Alloy (`xonotic-alloy`) runs as a DaemonSet, discovers pods on its local node through the Kubernetes API, and forwards their logs to Loki without privileged host filesystem mounts.
- Grafana (`xonotic-grafana`) reads Prometheus and Loki and serves provisioned metrics and logs dashboards through local port-forwarding. It runs as one `Recreate` replica and keeps `/var/lib/grafana` on a 2Gi PVC so migrations and provisioning state survive Pod replacement.
- kube-state-metrics (`xonotic-kube-state-metrics`) exposes Kubernetes object and state metrics such as pod phases, restarts, node conditions, Deployment state, and resource requests. Its custom-resource-state configuration also exports the primary Agones Fleet's `status.readyReplicas` as `agones_fleet_ready_replicas`. The ServiceAccount can read Fleet objects and discover their CRD; both permissions are required for the dynamic custom-resource collector.
- node-exporter (`xonotic-node-exporter`) runs once per node and exposes node CPU, memory, disk, filesystem, and network metrics.
- Prometheus also scrapes kubelet and cAdvisor metrics through the Kubernetes API server proxy for pod/container CPU and memory usage.

In short: Prometheus stores metrics, Loki stores logs, Alloy collects logs, and Grafana visualizes both.

Grafana uses SQLite only for this small dev deployment. WAL mode and bounded query/transaction retries reduce transient lock contention, and automatic suggested-plugin installation is disabled because the provisioned dashboards use built-in Prometheus and Loki visualizations. Periodic update checks and the unused Grafana-managed alert engine are disabled to avoid background work on the constrained node. Prometheus evaluates a small set of platform alerts locally; Alertmanager and notification delivery remain intentionally out of scope. Startup probes allow database migrations and provisioning to complete before liveness checks can restart the Pod.

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
kubectl rollout restart deployment/xonotic-kube-state-metrics -n xonotic-observability
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

Check the persistent Grafana volume:

```bash
kubectl get pvc xonotic-grafana-data -n xonotic-observability
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
- `Xonotic Allocator Operations`: backend HTTP request rate, backend request latency, allocation successes/failures, active match assignments, Xonotic namespace pod CPU/memory, pod restarts, RCON failures, map/mode verification failures, and Ready GameServers by Fleet.
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
- `agones_fleet_ready_replicas`: Ready GameServer replicas by primary-cluster Agones Fleet, exported from `Fleet.status.readyReplicas` through kube-state-metrics custom-resource-state configuration.

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

Ready GameServers by primary-cluster Fleet:

```promql
agones_fleet_ready_replicas{namespace="xonotic-agones"}
```

## Prometheus Alerts

Prometheus loads `alerts.yml` from the `xonotic-prometheus-alert-rules` ConfigMap and evaluates it every 15 seconds. View rule state under **Alerts** in Prometheus or query `GET /api/v1/rules`. Alerts can be `Inactive`, `Pending`, or `Firing`; this phase deliberately has no Alertmanager or external notifications.

### AllocatorBackendDown

- **Severity:** `critical`
- **Expression:** `up{job="allocator-backend"} == 0` for 2 minutes.
- **Meaning:** Prometheus cannot scrape the allocator backend, so allocation APIs and backend application metrics may be unavailable.
- **Dev test:** Scale `deployment/xonotic-allocator-backend` to zero, wait more than two minutes, and confirm the alert fires.
- **Recovery:** Reapply the backend manifests or scale the Deployment back to one, wait for readiness, and verify the target returns to `UP`.

### AllocationFailures

- **Severity:** `warning`
- **Expression:** `sum(increase(allocator_allocation_failures_total[10m])) > 0` for 1 minute.
- **Meaning:** At least one Agones allocation attempt failed recently, commonly because no Ready capacity exists or a regional API is unavailable.
- **Dev test:** With no Ready GameServer capacity, request one allocation from the Admin View and wait one minute.
- **Recovery:** Release stale assignments, restore regional Kubernetes access, or wait for FleetAutoscaler to restore Ready capacity; then retry allocation.

### RconFailures

- **Severity:** `warning`
- **Expression:** `sum(increase(allocator_rcon_command_failures_total[10m])) > 0` for 1 minute.
- **Meaning:** At least one allowlisted RCON command failed because of credentials, timeout, protocol, or endpoint reachability.
- **Dev test:** In the disposable dev environment, remove an allocated GameServer directly and invoke an Admin Control against its stale assignment.
- **Recovery:** Release the stale assignment and allocate a replacement, or restore the RCON Secret/backend configuration before retrying.

### MapModeVerificationFailures

- **Severity:** `warning`
- **Expression:** `sum(increase(allocator_map_mode_verification_failures_total[10m])) > 0` for 1 minute.
- **Meaning:** RCON configuration completed far enough to run `getstatus`, but the live map/mode did not match the requested combination.
- **Dev test:** Run the experimental verification script with a map/mode pair already known to fail in the current image, then wait one minute. Do not promote that pair.
- **Recovery:** Release the probe assignment and use a verified compatibility-matrix combination; investigate the RCON response and live `getstatus` output.

### PodRestartingTooMuch

- **Severity:** `warning`
- **Expression:** `sum by (namespace, pod, container) (increase(kube_pod_container_status_restarts_total{namespace=~"xonotic-allocator-backend|xonotic-agones|xonotic-observability"}[15m])) >= 3` for 2 minutes.
- **Meaning:** A platform container restarted at least three times in fifteen minutes, indicating a crash loop, failed startup, or resource problem.
- **Dev test:** In the disposable dev cluster, terminate PID 1 in the same test Pod three times and allow Kubernetes to restart its container each time.
- **Recovery:** Stop the fault injection, inspect `kubectl logs --previous` and Pod events, fix the startup/resource issue, then replace the Pod.

### NodeMemoryHigh

- **Severity:** `warning`
- **Expression:** `100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) > 85` for 10 minutes.
- **Meaning:** A node has sustained memory utilization above 85 percent, reducing headroom for rollouts and GameServer allocation.
- **Dev test:** Prefer a `promtool test rules` synthetic series. A real test requires a bounded temporary memory workload held above the threshold for ten minutes and should only be done while watching Node conditions.
- **Recovery:** Delete the test workload, release unused GameServers, reduce avoidable workloads, or resize the primary node through a reviewed Terraform change if normal idle use remains high.

### NoReadyGameServers

- **Severity:** `warning`
- **Expression:** `(agones_fleet_ready_replicas{namespace="xonotic-agones"} == 0) and on() (up{job="kube-state-metrics"} == 1)` for 2 minutes.
- **Meaning:** A primary-cluster Agones Fleet is observable but has no Ready GameServer capacity, so a new South America allocation cannot succeed immediately.
- **Dev test:** In the disposable dev cluster, temporarily remove the FleetAutoscaler and scale `xonotic-fleet` to zero; wait more than two minutes.
- **Recovery:** Reapply `platform/agones/manifests`, verify the FleetAutoscaler, and wait until `agones_fleet_ready_replicas` is at least one.

The No Ready expression is deliberately gated on a healthy kube-state-metrics scrape. Telemetry loss therefore does not masquerade as zero capacity. This first metric covers only the primary South America cluster; EU/NA need regional collection or central authenticated scraping before equivalent regional alerts can be reliable.

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
kubectl get pvc xonotic-grafana-data -n xonotic-observability
```

Grafana startup and restart stability:

```bash
kubectl rollout restart deployment/xonotic-grafana -n xonotic-observability
kubectl rollout status deployment/xonotic-grafana -n xonotic-observability --timeout=5m
kubectl logs deployment/xonotic-grafana -n xonotic-observability --since=10m | \
  grep -E 'SQLITE_BUSY|Datasource provisioning error' || true
```

The primary South America node uses `e2-standard-2` so the centralized control plane, observability stack, and warm GameServer have safe rollout headroom. The resize was approved after the earlier `e2-medium` node remained around 87-93% memory and 96% of allocatable CPU requests. To review the configured capacity without applying changes:

```bash
source scripts/env.sh
terraform -chdir=infra workspace select south-america
terraform -chdir=infra plan \
  -refresh=true \
  -var-file=regions/south-america.tfvars \
  -var="project_id=${GCP_PROJECT_ID}" \
  -var="region=${GCP_REGION}" \
  -var="zone=${GCP_ZONE}" \
  -var="cluster_name=${GKE_CLUSTER_NAME}"
```

The resize changed only `google_container_node_pool.primary` from `e2-medium` to `e2-standard-2`. GKE rolled the single node, so machine-type changes should still be treated as disruptive even when Terraform reports an in-place node-pool update.

GKE's managed metrics and logging agents also run in the primary cluster alongside this repository's Prometheus and Alloy stack. They provide Google Cloud integration but overlap with some project-level collection. Disabling managed GKE monitoring/logging could recover memory without a larger node, but it would remove that cloud-side visibility and requires a separate, reviewed infrastructure change; this observability hardening does not disable it automatically.

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

Verify the rule group, alert states, and Agones Ready metric:

```bash
curl -fsS http://127.0.0.1:9090/api/v1/rules | rg 'AllocatorBackendDown|AllocationFailures|RconFailures|MapModeVerificationFailures|PodRestartingTooMuch|NodeMemoryHigh|NoReadyGameServers'
curl -fsS http://127.0.0.1:9090/api/v1/alerts
curl -G -fsS http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=agones_fleet_ready_replicas{namespace="xonotic-agones"}'
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

## Agones Capacity Scope

kube-state-metrics watches the existing `agones.dev/v1` Fleet resource in the primary cluster and exports its real `status.readyReplicas` value. The Allocator Operations dashboard and `NoReadyGameServers` alert use this series; neither infers capacity from pod readiness or invents values.

Custom-resource-state collection requires both `list/watch` access to `agones.dev/fleets` and `list/watch` access to `apiextensions.k8s.io/customresourcedefinitions`. If the Fleet metric is absent, inspect kube-state-metrics logs for either permission before changing the Prometheus alert expression.

This first metric intentionally covers Ready capacity only. Allocated assignment behavior is still visible through allocator metrics, while richer Fleet desired/allocated/reserved series and equivalent EU/NA capacity require a later regional metrics design.

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
