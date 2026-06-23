# Infrastructure

This directory contains the minimal Terraform needed for the project: a small GCP foundation plus cost-conscious GKE Standard clusters for opt-in Xonotic game server regions.

The Terraform in this directory is implemented, but nothing in `infra/` creates real cloud resources until you run `terraform apply` against a real GCP project.

## What This Creates

Terraform in this directory creates:

- the required GCP APIs for this phase
- one zonal GKE Standard cluster described by the selected region/server-pool tfvars
- one small node pool for that cluster

It intentionally does not create:

- Artifact Registry
- Agones resources
- workload manifests
- GitHub Actions resources
- GitHub OIDC or Workload Identity Federation
- advanced networking such as a dedicated VPC, subnets, NAT, or firewall customization
- extra service accounts or broad IAM design

Exception for the current Agones phase:

- one narrow VPC firewall rule allowing UDP `26000` ingress so the first Agones `GameServer` is reachable through direct node access
- one narrow VPC firewall rule allowing UDP `7000-7010` ingress for the Fleet's dynamic host ports

## Required Variables

At minimum, set:

- `project_id`: the existing GCP project ID

The current deployment is represented by `default_server_pool_id` and `server_pools`. The default pool is `south-america-default`, which maps to the South America GKE/Agones setup documented in [`docs/region-server-pools.md`](/Users/n/Documents/Cloud/xonotic/docs/region-server-pools.md).

Region-specific tfvars files live under [`regions/`](/Users/n/Documents/Cloud/xonotic/infra/regions):

| Region script argument | Terraform tfvars | Terraform workspace | Pool ID | Cluster |
| --- | --- | --- | --- | --- |
| `south-america` | `regions/south-america.tfvars` | `south-america` | `south-america-default` | `xonotic-mvp` |
| `europe` | `regions/europe.tfvars` | `europe` | `europe-default` | `xonotic-eu` |
| `north-america` | `regions/north-america.tfvars` | `north-america` | `north-america-default` | `xonotic-na` |

Each tfvars file configures exactly one provisioned pool for that region. `project_id` is intentionally not committed in those files; the region scripts pass it from `GCP_PROJECT_ID`.

The other variables have practical defaults for a low-cost MVP and can be overridden if needed:

- `environment`: defaults to `mvp`
- `network_name`: defaults to `default`
- `subnetwork_name`: defaults to `default`
- `node_machine_type`: defaults to `e2-medium`
- `node_disk_size_gb`: defaults to `100`
- `node_disk_type`: defaults to `pd-standard`
- `node_count`: defaults to `1`

Compatibility variables `region`, `zone`, and `cluster_name` still exist for older South America local `terraform.tfvars` files, but new configuration should prefer `server_pools`. They are intentionally ignored for non-South America region tfvars so stale local overrides cannot point Europe or North America back at the primary cluster.

Use [`terraform.tfvars.example`](/Users/n/Documents/Cloud/xonotic/infra/terraform.tfvars.example) as the starting point for local values.

## How To Run

From this directory:

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

For region-oriented use from the repository root, prefer the scripts:

```bash
./scripts/up-region.sh europe
./scripts/up-region.sh north-america
./scripts/down-region.sh europe
./scripts/down-region.sh north-america
```

Those scripts select or create the matching Terraform workspace before planning/applying. This keeps regional state isolated so applying `europe` does not replace or destroy `south-america`.

After Terraform creates the cluster, `up-region.sh` also fetches kubeconfig credentials and deploys the regional game-server plane:

- Agones
- `xonotic-agones` namespace
- `xonotic-rcon` Secret from `XONOTIC_RCON_PASSWORD`
- Xonotic `Fleet`
- Xonotic `FleetAutoscaler`

It does not deploy the allocator backend, frontend, PostgreSQL, or observability stack into secondary regions.

You can inspect regional workspaces and outputs with:

```bash
terraform -chdir=infra workspace list
terraform -chdir=infra output default_server_pool
terraform -chdir=infra output server_pools
```

The scripts restore the previously active workspace when they exit, so the existing local workflow is less likely to be left pointing at the wrong region.

Cost warning: every additional region can create another GKE cluster and node pool. Always read the plan before confirming an apply or destroy.

## Local Operator Scripts

For cheap on-demand testing from the repository root, these scripts bring the cluster and the current Agones phase up only when needed and tear it down afterward.

Required environment variables:

- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_ZONE`
- `GKE_CLUSTER_NAME`
- `XONOTIC_RCON_PASSWORD`

Optional server-pool environment variables default to the current South America pool:

- `XONOTIC_SERVER_POOL_ID`
- `XONOTIC_SERVER_POOL_DISPLAY_NAME`
- `XONOTIC_SERVER_REGION`
- `XONOTIC_AGONES_NAMESPACE`
- `XONOTIC_FLEET_NAME`
- `XONOTIC_UDP_PORT_RANGE`

You can either export them in your shell or copy `scripts/env.sh.example` to `scripts/env.sh` and edit the values there. The scripts source `scripts/env.sh` automatically if it exists.

Bring up the primary control-plane environment and South America game-server plane:

```bash
./scripts/up.sh
```

`./scripts/up.sh` now does all of the following:

- selects the `south-america` Terraform workspace and applies the South America cluster/firewall configuration
- imports an already-existing legacy South America cluster, node pool, or firewall rules into that workspace when their Terraform bindings are missing
- fetches kubeconfig credentials
- installs or updates Agones with the repo's current Fleet-phase settings, including the narrow dynamic port range
- applies the Xonotic Agones namespace, `Fleet`, and `FleetAutoscaler`
- recreates the RCON Kubernetes Secrets from `XONOTIC_RCON_PASSWORD`
- creates the PostgreSQL, admin-auth, and multi-cluster kubeconfig Secrets
- deploys PostgreSQL and waits for its Pod to become Ready
- recreates the Fleet `GameServer` instances after the Agones upgrade so their live host ports match the repo's constrained dynamic port range
- applies the allocator backend namespace, RBAC, Deployment, and Service
- applies the allocator frontend Deployment and Service
- waits for PostgreSQL, backend, and frontend Pods to become Ready
- prints backend/frontend port-forward commands when complete

Prometheus and Grafana remain a separate, optional deployment:

```bash
kubectl apply -k platform/observability
```

Terraform also exposes server-pool metadata for the active deployment:

```bash
terraform -chdir=infra output default_server_pool
terraform -chdir=infra output server_pools
```

Tear the current test path and infra down:

```bash
./scripts/down.sh
```

The script distinction is deliberate:

- `./scripts/up.sh`: primary South America game-server plane plus PostgreSQL, backend, and frontend
- `./scripts/up-region.sh europe`: Europe game-server plane only
- `./scripts/up-region.sh north-america`: North America game-server plane only
- `./scripts/down.sh`: destroys the primary South America workspace
- `./scripts/down-region.sh <region>`: destroys only the selected regional workspace

If you prefer a tfvars file:

```bash
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

## Before Any Kubernetes Deployment

You cannot apply workload manifests until all of the following are true:

- Terraform has successfully created the cluster and node pool
- you have run the generated `gcloud container clusters get-credentials ...` command
- `kubectl get nodes` succeeds against the new cluster

The connectivity checkpoint under [`platform/connectivity-checkpoint/README.md`](/Users/n/Documents/Cloud/xonotic/platform/connectivity-checkpoint/README.md) starts only after those prerequisites are satisfied.

## How To Destroy

```bash
terraform destroy
```

If you use region workspaces directly, select the intended workspace first and pass the matching tfvars:

```bash
terraform -chdir=infra workspace select europe
terraform -chdir=infra plan -destroy -var-file=regions/europe.tfvars -var="project_id=${GCP_PROJECT_ID}"
terraform -chdir=infra destroy -var-file=regions/europe.tfvars -var="project_id=${GCP_PROJECT_ID}"
```

Do not destroy from an unknown active workspace. `terraform -chdir=infra workspace show` should match the region you intend to tear down.

Notes:

- the cluster and node pool are destroyed
- API enablement is left on intentionally; Terraform does not disable services on destroy in this MVP setup

## Cost-Conscious Notes

- the cluster is zonal, not regional, to avoid multiplying control-plane and node costs
- the node pool defaults to a single `e2-medium` node
- node disk defaults to `100 GB` on `pd-standard`; this is still a low-cost choice, but it leaves enough allocatable ephemeral storage for the first Agones controller footprint on a single-node dev cluster
- this is a deliberate MVP baseline, not a capacity target for real gameplay load
- once Agones and the actual game workload are added, the machine type may need to increase

## Agones Disk Sizing Note

The earlier `30 GB` node disk was enough for the plain Kubernetes connectivity checkpoint because that phase ran only the Xonotic workload plus the normal cluster system pods.

For the first Agones phase on a single-node cluster, Agones adds controller pods that request significant `ephemeral-storage`. In practice, the default Agones controller and extensions requests can exceed the allocatable ephemeral storage left on a `30 GB` node disk after system reservations, image storage, and kubelet overhead. The `100 GB` default is a practical dev-cluster adjustment that keeps the cluster simple while giving the scheduler enough room to place those pods.

## Agones Networking Note

The first Agones `GameServer` reference in this repo does not use a `LoadBalancer` Service. It uses direct node access through Agones `hostPort` publishing on UDP `26000`.

The Fleet phase also does not use a `LoadBalancer` Service. It uses dynamic Agones host ports in the explicit range `7000-7010`.

Because there is no Kubernetes `Service` of type `LoadBalancer` in either path, GKE does not create the equivalent `k8s-fw-*` ingress firewall rules for you. Terraform now creates:

- one narrow VPC firewall rule for UDP `26000` for the single-`GameServer` reference
- one narrow VPC firewall rule for UDP `7000-7010` for the Fleet phase

## Assumptions

- the GCP project already exists and billing is already enabled
- the default VPC and default subnetwork exist and are acceptable for the first iteration
- the operator running Terraform already has enough GCP permissions to enable APIs and create GKE resources
- local Terraform state is acceptable for this phase
- the initial default deployment target is a South America zone, specifically `southamerica-west1-a`

## Intentionally Deferred

- dedicated VPC and subnet design
- remote Terraform state
- GitHub to GCP federation setup
- cluster access IAM design
- dedicated node service accounts
- Agones installation
- production-grade cross-cluster identity instead of dev kubeconfig service-account tokens
- observability stack and alerting
- multi-environment layout
