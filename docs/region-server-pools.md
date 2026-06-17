# Region Server Pools

The platform is starting to model game server capacity as region-aware server pools. This is an application and infrastructure configuration abstraction only; the current dev deployment still uses one GKE cluster and one Agones Fleet.

## Region

A region is a player-facing placement concept such as `south-america`, `north-america`, or `europe`. It describes where tournament match capacity should live from an operator/player point of view.

The current implemented region is:

- `south-america`

## Server Pool

A server pool is the concrete backend for a region. It maps an operator-facing pool ID to the Kubernetes and Agones resources that can allocate Xonotic match servers.

The current pool is:

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

## Current South America Mapping

Terraform now exposes the current cluster as the default server pool through `server_pools` and `default_server_pool_id`. The resolved pool metadata is available through these outputs:

```bash
terraform -chdir=infra output default_server_pool_id
terraform -chdir=infra output default_server_pool
terraform -chdir=infra output server_pools
```

`scripts/up.sh` and `scripts/down.sh` use the same pool defaults when they generate a local `infra/terraform.tfvars` file. By default, they still target the existing South America GKE/Agones setup and do not increase Fleet capacity.

The Admin View also surfaces runtime capacity per enabled server pool. The backend reads the configured Agones Fleet for each pool and reports Desired, Current, Ready, Allocated, and Reserved replicas so operators can see when a region has no Ready servers before attempting match allocation.

## Adding Another Region Later

Adding Europe or North America later would require more than adding another entry to `server_pools`. A real multi-region phase should add:

- a second GKE cluster in the target GCP region/zone
- Agones installed in that cluster
- a Xonotic Fleet and FleetAutoscaler for that region
- matching UDP firewall rules for that pool's port range
- backend allocation routing that chooses the correct Kubernetes client/cluster for the selected pool
- deployment automation for backend/frontend configuration across regions
- observability labels and dashboards grouped by pool/region

Until that work exists, only `south-america-default` should be treated as an active server pool.
