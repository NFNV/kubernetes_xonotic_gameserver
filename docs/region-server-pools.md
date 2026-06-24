# Region Server Pools

The platform models game server capacity as region-aware server pools. South America hosts the central control plane, while South America, Europe, and North America each provide an independent GKE/Agones game-server plane.

## Region

A region is a player-facing placement concept such as `south-america`, `north-america`, or `europe`. It describes where tournament match capacity should live from an operator/player point of view.

The currently provisioned regions are `south-america`, `europe`, and `north-america`.

## Server Pool

A server pool is the concrete backend for a region. It maps an operator-facing pool ID to the Kubernetes and Agones resources that can allocate Xonotic match servers.

The current pools are:

| Pool ID | Display name | GCP region/zone | GKE cluster | Agones target |
| --- | --- | --- | --- | --- |
| `south-america-default` | South America - Default | `southamerica-west1` / `southamerica-west1-a` | `xonotic-mvp` | `xonotic-agones/xonotic-fleet` |
| `europe-default` | Europe - Default | `europe-west1` / `europe-west1-b` | `xonotic-eu` | `xonotic-agones/xonotic-fleet` |
| `north-america-default` | North America - Default | `us-central1` / `us-central1-a` | `xonotic-na` | `xonotic-agones/xonotic-fleet` |

All three use dynamic UDP ports `7000-7010`. Only the South America cluster runs the allocator backend, frontend, PostgreSQL, and observability workloads.

## Regional Terraform Definitions

The shared Terraform code under [`infra/`](/Users/n/Documents/Cloud/xonotic/infra) can be pointed at one region at a time with region-specific tfvars files:

| Region | Terraform tfvars | Workspace | Provisioned pool ID | GCP target |
| --- | --- | --- | --- | --- |
| `south-america` | `infra/regions/south-america.tfvars` | `south-america` | `south-america-default` | `southamerica-west1-a` |
| `europe` | `infra/regions/europe.tfvars` | `europe` | `europe-default` | `europe-west1-b` |
| `north-america` | `infra/regions/north-america.tfvars` | `north-america` | `north-america-default` | `us-central1-a` |

Each region file configures exactly one provisioned server pool for that region. The Terraform code is not duplicated; the pool metadata, cluster name, zone, and UDP port range come from the selected tfvars file.

The region scripts are explicit and opt-in:

```bash
./scripts/up-region.sh europe
./scripts/up-region.sh north-america

./scripts/down-region.sh europe
./scripts/down-region.sh north-america
```

They use Terraform workspaces named after the region, so a Europe apply uses the `europe` workspace and does not overwrite South America state. The scripts print the selected region, tfvars file, workspace, and a cost warning, then apply without an interactive confirmation prompt.

`up-region.sh` temporarily switches Kubernetes context while deploying that region, then restores the context that was active when the script started. This keeps primary backend/frontend commands pointed at South America during normal operation.

After Terraform creates the cluster, `up-region.sh` fetches kubeconfig credentials and deploys the regional game-server plane:

- Agones
- `xonotic-agones` namespace
- required `xonotic-rcon` Secret
- Xonotic `Fleet`
- Xonotic `FleetAutoscaler`

It intentionally does not deploy the allocator backend, frontend, PostgreSQL, or observability stack into secondary regions.

It also applies the least-privilege `xonotic-regional-allocator` ServiceAccount and namespaced RBAC used by the central backend. That identity can create/read `GameServerAllocation` resources, read Fleet/GameServer state, and delete allocated GameServers in `xonotic-agones`; it has no cluster-wide permissions.

Useful inspection commands:

```bash
terraform -chdir=infra workspace list
terraform -chdir=infra output default_server_pool
terraform -chdir=infra output server_pools
```

Important: `./scripts/up.sh` and `./scripts/down.sh` remain the current full South America dev workflow. They still handle Terraform plus Agones, Fleet, secrets, PostgreSQL, backend, and frontend. The new region scripts are regional game-server-plane controls and intentionally do not deploy duplicate central control-plane stacks into Europe or North America.

In short:

- `./scripts/up.sh` = primary control plane + South America game-server plane
- `./scripts/up-region.sh europe` = Europe game-server plane only
- `./scripts/up-region.sh north-america` = North America game-server plane only

Prometheus/Grafana are optional and remain deployed separately with `kubectl apply -k platform/observability`.

## Central Multi-Cluster Control Plane

The allocator backend remains in South America. It selects a Kubernetes client from the match's `requested_server_pool_id`:

- `south-america-default` uses `XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT`
- `europe-default` uses `XONOTIC_EUROPE_KUBE_CONTEXT`
- `north-america-default` uses `XONOTIC_NORTH_AMERICA_KUBE_CONTEXT`

`scripts/build-multicluster-kubeconfig.sh` reads the operator's three GKE contexts, ensures regional allocator RBAC exists, and writes a gitignored kubeconfig containing only the regional API endpoints, CA certificates, and namespaced allocator service-account tokens. `scripts/up.sh` stores that file in the `xonotic-multicluster-kubeconfig` Kubernetes Secret and mounts it read-only into the backend Pod.

Get or refresh the source contexts:

```bash
gcloud container clusters get-credentials xonotic-mvp \
  --zone southamerica-west1-a --project "${GCP_PROJECT_ID}"
gcloud container clusters get-credentials xonotic-eu \
  --zone europe-west1-b --project "${GCP_PROJECT_ID}"
gcloud container clusters get-credentials xonotic-na \
  --zone us-central1-a --project "${GCP_PROJECT_ID}"

./scripts/build-multicluster-kubeconfig.sh
```

The generated kubeconfig is a dev-cluster credential artifact and must not be committed. Rebuild it after recreating a regional cluster because that cluster receives a new API endpoint, CA, and service-account token.

During allocation, the backend creates the `GameServerAllocation` in the selected context, then configures and verifies the returned public Xonotic endpoint through RCON and `getstatus`. The assignment stores its pool, region, cluster, namespace, and Fleet metadata. Release, result-save cleanup, release-all, and tournament finalization use that assignment metadata to delete the GameServer from the same regional cluster.

The capacity endpoint queries each configured Fleet with its own context. A failed or unreachable regional context produces an `unavailable` pool row without breaking capacity results for the other regions.

## Capacity States

`Ready` means Agones has warm GameServers available for allocation. `Allocated` means GameServers are already assigned to active match/session infrastructure and are consuming Fleet capacity.

The capacity endpoint reports these operator-facing states:

| State | Meaning | Operator action |
| --- | --- | --- |
| `available` | The pool is provisioned and has at least one Ready GameServer. | Ready to allocate match servers. |
| `no-ready-capacity` | The pool is provisioned, but Ready GameServers are currently `0`. | Wait for FleetAutoscaler, release an active match server, or increase pool capacity. |
| `not-provisioned` | A configured future pool has no deployed regional infrastructure. | Create regional infrastructure before enabling this pool. |
| `unavailable` | The backend could not query the configured Agones Fleet. | Check backend Kubernetes access and Agones Fleet health for this pool. |

## Adding Another Region Later

Adding another provisioned region requires:

- a GKE cluster in the target GCP region/zone
- Agones installed in that cluster
- a Xonotic Fleet and FleetAutoscaler for that region
- matching UDP firewall rules for that pool's port range
- the regional allocator ServiceAccount/RBAC and kubeconfig context
- a backend server-pool config entry and context environment variable
- observability labels and dashboards grouped by pool/region
- public DNS/Ingress or another operator-approved discovery model for regional endpoints
