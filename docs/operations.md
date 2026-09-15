# Operations And Troubleshooting

This runbook covers the dev/portfolio deployment. All services remain private and are accessed through `kubectl port-forward`.

## Contexts And Namespaces

```bash
export SA_CONTEXT="gke_xonotic-gameserver_southamerica-west1-a_xonotic-mvp"
export EU_CONTEXT="gke_xonotic-gameserver_europe-west1-b_xonotic-eu"
export NA_CONTEXT="gke_xonotic-gameserver_us-central1-a_xonotic-na"
```

| Workload | Namespace |
| --- | --- |
| Control plane and PostgreSQL | `xonotic-allocator-backend` |
| Primary observability | `xonotic-observability` |
| Regional Fleet and GameServers | `xonotic-agones` |
| Agones controllers | `agones-system` |

## Platform Health

Check the primary control plane:

```bash
kubectl --context "$SA_CONTEXT" get pods,deploy,svc -n xonotic-allocator-backend
kubectl --context "$SA_CONTEXT" get pods,deploy,daemonset,svc -n xonotic-observability
kubectl --context "$SA_CONTEXT" get pods -n agones-system
```

Check Fleet and GameServer capacity in each online region:

```bash
kubectl --context "$SA_CONTEXT" get fleet,fleetautoscaler,gameserver -n xonotic-agones -o wide
kubectl --context "$EU_CONTEXT" get fleet,fleetautoscaler,gameserver -n xonotic-agones -o wide
kubectl --context "$NA_CONTEXT" get fleet,fleetautoscaler,gameserver -n xonotic-agones -o wide
```

`Ready` is warm allocatable capacity. `Allocated` is capacity assigned to an active session or match.

## Local Access

Run each required port-forward in its own terminal:

```bash
kubectl --context "$SA_CONTEXT" port-forward -n xonotic-allocator-backend service/xonotic-allocator-frontend 18080:8080
kubectl --context "$SA_CONTEXT" port-forward -n xonotic-allocator-backend service/xonotic-allocator-backend 18082:8080
kubectl --context "$SA_CONTEXT" port-forward -n xonotic-observability service/xonotic-grafana 3000:3000
kubectl --context "$SA_CONTEXT" port-forward -n xonotic-observability service/xonotic-prometheus 9090:9090
```

| Service | URL |
| --- | --- |
| Frontend | `http://127.0.0.1:18080` |
| Backend | `http://127.0.0.1:18082` |
| Grafana | `http://127.0.0.1:3000` |
| Prometheus | `http://127.0.0.1:9090` |

If a local port is occupied, change only the left side, for example `29090:9090`. A port-forward stops when its terminal exits or receives `Ctrl+C`.

## Release Verification

`./scripts/up.sh` now selects the newest successfully published automatic `master` release before provisioning, verifies all three GHCR SHA images, and deploys that same SHA to the South America Fleet, backend, and frontend. It prints the version and full SHA. A newer merge still publishing is not selected prematurely.

To pin a previously published release for rollback:

```bash
XONOTIC_RELEASE_SHA="<full-40-character-published-sha>" ./scripts/up.sh
```

If GitHub or GHCR cannot be queried, `up.sh` stops before Terraform changes. It never falls back to the fixed development tags. The local manifests must remain compatible with the chosen image release; use a matching checkout when rolling back across manifest changes.

Verify the backend release identity:

```bash
curl -fsS http://127.0.0.1:18082/version | jq
```

Check immutable images running in the control plane and primary Fleet:

```bash
kubectl --context "$SA_CONTEXT" get deployment xonotic-allocator-backend \
  -n xonotic-allocator-backend -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl --context "$SA_CONTEXT" get deployment xonotic-allocator-frontend \
  -n xonotic-allocator-backend -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
kubectl --context "$SA_CONTEXT" get fleet xonotic-fleet \
  -n xonotic-agones -o jsonpath='{.spec.template.spec.template.spec.containers[0].image}{"\n"}'
```

Both `up.sh` bootstrap and manual GitHub deployments should show `sha-<40-character-git-sha>`, not a mutable convenience tag. An already Allocated GameServer can still show an older image until that match is released; `up.sh` does not delete it.

## Logs And Alerts

Backend and GameServer logs:

```bash
kubectl --context "$SA_CONTEXT" logs -n xonotic-allocator-backend \
  deployment/xonotic-allocator-backend --tail=200

export GAMESERVER_NAME="$(kubectl --context "$SA_CONTEXT" get gameserver -n xonotic-agones \
  -o jsonpath='{.items[0].metadata.name}')"
kubectl --context "$SA_CONTEXT" logs -n xonotic-agones "$GAMESERVER_NAME" -c server --tail=200
```

Prometheus target and alert state:

```bash
curl -fsS http://127.0.0.1:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, health}'
curl -fsS http://127.0.0.1:9090/api/v1/alerts | jq '.data.alerts[] | {alert: .labels.alertname, state, value}'
```

Alert expressions, safe tests, and recovery details are documented in the [observability guide](../platform/observability/README.md#prometheus-alerts).

## Failure Diagnosis

### No Ready GameServers

```bash
kubectl --context "$SA_CONTEXT" get fleet,fleetautoscaler,gameserver -n xonotic-agones
kubectl --context "$SA_CONTEXT" describe fleet xonotic-fleet -n xonotic-agones
```

Release stale Allocated servers or redeploy the selected primary Fleet release through **Deploy Regional Game Plane**. Do not apply the static `platform/agones/manifests` Kustomization directly: its checkpoint tag is not the release selected by `up.sh`.

```bash
kubectl --context "$SA_CONTEXT" wait -n xonotic-agones \
  --for=jsonpath='{.status.readyReplicas}'=1 fleet/xonotic-fleet --timeout=300s
```

### Backend Unavailable

```bash
kubectl --context "$SA_CONTEXT" get pod -n xonotic-allocator-backend
kubectl --context "$SA_CONTEXT" describe deployment xonotic-allocator-backend -n xonotic-allocator-backend
kubectl --context "$SA_CONTEXT" logs deployment/xonotic-allocator-backend -n xonotic-allocator-backend --tail=200
kubectl --context "$SA_CONTEXT" logs deployment/xonotic-postgres -n xonotic-allocator-backend --tail=100
```

Check for missing Secret keys without printing values:

```bash
kubectl --context "$SA_CONTEXT" get secret xonotic-admin-auth -n xonotic-allocator-backend \
  -o go-template='{{range $k, $_ := .data}}{{println $k}}{{end}}'
```

### RCON Failure

Confirm the RCON Secrets exist in the control plane and target region, then inspect backend and GameServer logs:

```bash
kubectl --context "$SA_CONTEXT" get secret xonotic-rcon -n xonotic-allocator-backend
kubectl --context "$SA_CONTEXT" get secret xonotic-rcon -n xonotic-agones
kubectl --context "$SA_CONTEXT" logs deployment/xonotic-allocator-backend \
  -n xonotic-allocator-backend --since=15m
```

Do not print the Secret. Release the stale assignment, restore matching local `XONOTIC_RCON_PASSWORD` configuration, and rerun `./scripts/up.sh` if the Secret must be recreated.

### Allocation Failure

Check capacity before treating a request as an Agones failure:

```bash
curl -fsS http://127.0.0.1:18082/server-pools/capacity | \
  jq '.items[] | {server_pool_id, capacity_state, ready_replicas, allocated_replicas, warning_message}'
curl -G -fsS http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=sum by (reason) (increase(allocator_allocation_failures_total[10m]))'
```

A tournament `409 no_ready_servers` response is a capacity preflight, not a failed Agones allocation attempt. Restore Ready capacity first. For `5xx` responses, inspect backend logs and the selected region's Agones controllers.

### Region Offline Or Unavailable

```bash
gcloud container clusters list --project "$GCP_PROJECT_ID"
curl -fsS http://127.0.0.1:18082/server-pools/capacity | jq '.items[] | {server_pool_id, capacity_state, warning_message}'
```

If the cluster is intentionally down, bring up only that game-server plane. After recreating a regional cluster, rebuild and apply the scoped multicluster kubeconfig so the central backend receives its new endpoint and credentials:

```bash
./scripts/up-region.sh europe
./scripts/build-multicluster-kubeconfig.sh
./scripts/apply-multicluster-kubeconfig-secret.sh
kubectl --context "$SA_CONTEXT" rollout restart deployment/xonotic-allocator-backend -n xonotic-allocator-backend
```

### Grafana Or Prometheus Unavailable

```bash
kubectl --context "$SA_CONTEXT" get pod,deploy,daemonset -n xonotic-observability
kubectl --context "$SA_CONTEXT" logs deployment/xonotic-grafana -n xonotic-observability --tail=200
kubectl --context "$SA_CONTEXT" logs deployment/xonotic-prometheus -n xonotic-observability --tail=200
kubectl --context "$SA_CONTEXT" apply -k platform/observability
```

Prometheus, Grafana, and Loki remain internal `ClusterIP` services. A refused local connection normally means the port-forward is not running or its Pod is not Ready.

## Teardown And Cost Control

The clusters are intentionally not expected to run continuously. Bring up and destroy each environment explicitly:

```bash
./scripts/up.sh
./scripts/down.sh

./scripts/up-region.sh europe
./scripts/down-region.sh europe

./scripts/up-region.sh north-america
./scripts/down-region.sh north-america
```

`down.sh` removes primary workloads and invokes Terraform destroy for the `south-america` workspace. Each `down-region.sh` destroys only its selected regional workspace. Run region operations sequentially because they share one local Terraform working directory.

After cluster teardown, GHCR images, repository/GitHub configuration, enabled GCP APIs, local Terraform state/workspaces, and ignored local files remain. Cluster workloads, node pools, GKE clusters, and Terraform-managed regional firewall rules are removed. Preserve Terraform state files: the local lifecycle scripts require them to identify the resources they own.
