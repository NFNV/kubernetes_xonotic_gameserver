# Infrastructure

This directory contains the minimal Terraform needed for Phase 1 of the project: a small GCP foundation plus one cost-conscious GKE Standard cluster for the MVP.

The Terraform in this directory is implemented, but nothing in `infra/` creates real cloud resources until you run `terraform apply` against a real GCP project.

## What This Creates

Terraform in this directory creates:

- the required GCP APIs for this phase
- one zonal GKE Standard cluster
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

The other variables have practical defaults for a low-cost MVP and can be overridden if needed:

- `region`: defaults to `southamerica-west1`
- `zone`: defaults to `southamerica-west1-a`
- `environment`: defaults to `mvp`
- `cluster_name`: defaults to `xonotic-mvp`
- `network_name`: defaults to `default`
- `subnetwork_name`: defaults to `default`
- `node_machine_type`: defaults to `e2-medium`
- `node_disk_size_gb`: defaults to `100`
- `node_disk_type`: defaults to `pd-standard`
- `node_count`: defaults to `1`

Use [`terraform.tfvars.example`](/Users/n/Documents/Cloud/xonotic/infra/terraform.tfvars.example) as the starting point for local values.

## How To Run

From this directory:

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

## Local Operator Scripts

For cheap on-demand testing from the repository root, these scripts bring the cluster and the current Agones phase up only when needed and tear it down afterward.

Required environment variables:

- `GCP_PROJECT_ID`
- `GCP_REGION`
- `GCP_ZONE`
- `GKE_CLUSTER_NAME`

You can either export them in your shell or copy `scripts/env.sh.example` to `scripts/env.sh` and edit the values there. The scripts source `scripts/env.sh` automatically if it exists.

Bring the infra and the current Agones Fleet-and-allocation phase up:

```bash
./scripts/up.sh
```

`./scripts/up.sh` now does all of the following:

- applies the Terraform for the cluster and firewall rule
- fetches kubeconfig credentials
- installs or updates Agones with the repo's current Fleet-phase settings, including the narrow dynamic port range
- applies the Xonotic Agones namespace and `Fleet`
- waits for the Agones controller deployments and Fleet readiness, then prints Fleet and `GameServer` state

Tear the current test path and infra down:

```bash
./scripts/down.sh
```

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
- observability stack and alerting
- multi-environment or multi-cluster layout
