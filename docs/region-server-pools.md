# Region Server Pools

The platform models game server capacity as region-aware server pools. This is now represented in both the application configuration and the Terraform/operator workflow, but the current dev deployment still has only one provisioned, allocatable GKE/Agones region by default.

## Region

A region is a player-facing placement concept such as `south-america`, `north-america`, or `europe`. It describes where tournament match capacity should live from an operator/player point of view.

The current provisioned region is:

- `south-america`

The current planned regions are:

- `europe`
- `north-america`

## Server Pool

A server pool is the concrete backend for a region. It maps an operator-facing pool ID to the Kubernetes and Agones resources that can allocate Xonotic match servers.

The current provisioned pool is:

| Field | Value |
| --- | --- |
| Pool ID | `south-america-default` |
| Display name | `South America - Default` |
| Region | `south-america` |
| GCP region | `southamerica-west1` |
| GCP zone | `southamerica-west1-a` |
| GKE cluster | `xonotic-mvp` |
| Agones namespace | `xonotic-agones` |
| Agones Fleet | `xonotic-fleet` |
| UDP port range | `7000-7010` |

The current simulated pools are:

| Pool ID | Display name | Region | Provider | Provisioned | Enabled | Status |
| --- | --- | --- | --- | --- | --- | --- |
| `europe-simulated` | `Europe - Simulated` | `europe` | `gcp` | `false` | `false` | `not-provisioned` |
| `north-america-simulated` | `North America - Simulated` | `north-america` | `gcp` | `false` | `false` | `not-provisioned` |

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
./scripts/up-region.sh south-america
./scripts/up-region.sh europe
./scripts/up-region.sh north-america

./scripts/down-region.sh europe
./scripts/down-region.sh north-america
```

They use Terraform workspaces named after the region, so a Europe apply uses the `europe` workspace and does not overwrite South America state. The scripts print the selected region, tfvars file, workspace, and a cost warning before applying or destroying.

After Terraform creates the cluster, `up-region.sh` fetches kubeconfig credentials and deploys the regional game-server plane:

- Agones
- `xonotic-agones` namespace
- required `xonotic-rcon` Secret
- Xonotic `Fleet`
- Xonotic `FleetAutoscaler`

It intentionally does not deploy the allocator backend, frontend, PostgreSQL, or observability stack into secondary regions.

Useful inspection commands:

```bash
terraform -chdir=infra workspace list
terraform -chdir=infra output default_server_pool
terraform -chdir=infra output server_pools
```

Important: `./scripts/up.sh` and `./scripts/down.sh` remain the current full South America dev workflow. They still handle Terraform plus Agones, Fleet, secrets, PostgreSQL, backend, and frontend. The new region scripts are regional game-server-plane controls and intentionally do not deploy duplicate central control-plane stacks into Europe or North America.

## Current South America Mapping

Terraform now exposes the current cluster as the default server pool through `server_pools` and `default_server_pool_id`. The resolved pool metadata is available through these outputs:

```bash
terraform -chdir=infra output default_server_pool_id
terraform -chdir=infra output default_server_pool
terraform -chdir=infra output server_pools
```

`scripts/up.sh` and `scripts/down.sh` use the same pool defaults when they generate a local `infra/terraform.tfvars` file. By default, they still target the existing South America GKE/Agones setup and do not increase Fleet capacity.

The Admin View also surfaces runtime capacity per configured server pool. For the South America pool, the backend reads the configured Agones Fleet and reports Desired, Current, Ready, Allocated, and Reserved replicas so operators can see when the region has no Ready servers before attempting match allocation. For simulated pools, the backend returns `not-provisioned` and does not query Kubernetes.

## Capacity States

`Ready` means Agones has warm GameServers available for allocation. `Allocated` means GameServers are already assigned to active match/session infrastructure and are consuming Fleet capacity.

The capacity endpoint reports these operator-facing states:

| State | Meaning | Operator action |
| --- | --- | --- |
| `available` | The pool is provisioned and has at least one Ready GameServer. | Ready to allocate match servers. |
| `no-ready-capacity` | The pool is provisioned, but Ready GameServers are currently `0`. | Wait for FleetAutoscaler, release an active match server, or increase pool capacity. |
| `not-provisioned` | The pool is planned/simulated and has no deployed regional infrastructure. | Create regional infrastructure before enabling this pool. |
| `unavailable` | The backend could not query the configured Agones Fleet. | Check backend Kubernetes access and Agones Fleet health for this pool. |

## Simulation Mode

Simulation mode lets the control-plane UX show future regional server pools without deploying more clusters or increasing cloud cost. Europe and North America are intentionally visible as planned regions, but they are not allocatable and do not have fake capacity, fake endpoints, or fake GameServers.

If an allocation request targets a simulated pool, the backend rejects it with a clear `server_pool_not_provisioned` error. This keeps the UI honest while still demonstrating how the platform would present multi-region operations once those regions are backed by real infrastructure.

The new `europe-default` and `north-america-default` Terraform tfvars are infrastructure definitions for future real pools. `up-region.sh` can now provision their cluster and deploy Agones/Fleet capacity, but those pools are not automatically exposed as enabled backend pools and do not make the backend capable of allocating cross-cluster servers by themselves.

## Adding Another Region Later

Activating Europe or North America later would require more than changing `enabled` to `true`. A real multi-region phase should add:

- a second GKE cluster in the target GCP region/zone, created with the matching region script
- Agones installed in that cluster
- a Xonotic Fleet and FleetAutoscaler for that region
- matching UDP firewall rules for that pool's port range
- backend allocation routing that chooses the correct Kubernetes client/cluster for the selected pool
- deployment automation for backend/frontend configuration across regions
- observability labels and dashboards grouped by pool/region
- public DNS/Ingress or another operator-approved discovery model for regional endpoints

Until that work exists, only `south-america-default` should be treated as an active server pool.
