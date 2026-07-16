# CI/CD

The GitHub Actions pipeline validates and packages the platform while every GKE cluster is offline, then exposes explicit manual release workflows for clusters that an operator has brought online. It complements the local cost-control scripts; it does not replace them.

## Delivery Model

```text
Pull request
    |
    v
Backend/frontend validation + Terraform validation + manifest validation
    |
    v
Container builds without push
    |
    v
Merge to master -> coordinated SHA-tagged images -> GHCR
    |
    +-------------------------------+
    |                               |
Clusters offline               Cluster online
CI remains healthy             Manual deployment workflow
No deployment required         Rollout checks + smoke tests
```

| Operation | Trigger | Infrastructure required |
| --- | --- | --- |
| Pull-request validation | Automatic | No |
| Publish immutable images | Successful CI on `master` | No |
| Deploy South America control plane | Manual | `xonotic-mvp` |
| Deploy regional GameServer Fleet | Manual | Selected regional cluster |
| Terraform regional configuration plan | PR or manual | No |
| Terraform apply/destroy | Local scripts only | GCP credentials |

The application workflows deliberately do not create or destroy clusters. A missing cluster is reported as **not provisioned** and the deployment is skipped successfully. Authentication, connectivity, and rollout errors remain failures.

## Workflows

### CI

`.github/workflows/ci.yml` runs on pull requests and pushes to `master`:

- installs backend dependencies and compiles the Python source
- runs `npm ci` and the frontend production build
- checks Terraform formatting, initializes without a backend, and validates the configuration
- checks shell syntax, ShellCheck findings, and workflow YAML with `actionlint`
- renders each Kustomize root and validates standard Kubernetes schemas
- validates the provisioned Grafana dashboard JSON and Prometheus, Loki, and Alloy configuration
- builds all three `linux/amd64` images without pushing on pull requests

The repository does not currently contain pytest tests or a frontend `npm test` script. CI reports compilation and production-build health honestly rather than presenting nonexistent test suites as coverage.

### Image Publication

`.github/workflows/publish-images.yml` runs only after successful `master` CI or through explicit manual dispatch. It publishes a coordinated release bundle:

```text
ghcr.io/nfnv/xonotic-allocator-backend:sha-<40-character-sha>
ghcr.io/nfnv/xonotic-allocator-frontend:sha-<40-character-sha>
ghcr.io/nfnv/xonotic-server:sha-<40-character-sha>
```

`master` is a convenience pointer. Kubernetes release workflows accept only a full Git SHA and deploy only `sha-...` tags. Each image includes OCI source, revision, creation-time, and description labels; the workflow summary records its digest.

### Control-Plane Deployment

`.github/workflows/deploy-control-plane.yml` targets only `xonotic-mvp` in `southamerica-west1-a`. It requires a full `image_sha` and the protected `control-plane-dev` environment.

The workflow checks the cluster and required runtime Secrets, applies PostgreSQL, immutable backend/frontend releases, and observability, waits for workloads, then performs in-cluster HTTP smoke tests. It never prints or replaces PostgreSQL, admin-auth, RCON, or multicluster kubeconfig Secret values. Use `./scripts/up.sh` to bootstrap or recreate those runtime Secrets before the first GitHub deployment.

### Regional GameServer Deployment

`.github/workflows/deploy-game-plane.yml` accepts `south-america`, `europe`, `north-america`, or `all`. Each selected region runs independently with a region-specific protected environment and concurrency lock.

The workflow verifies Agones and RCON prerequisites, applies the Fleet/FleetAutoscaler with an immutable GameServer image, and waits for a Ready GameServer using that image. Agones RollingUpdate replaces Ready capacity while preserving Allocated GameServers until they are released. Re-running the workflow with an older SHA rolls the Fleet template back without deliberately terminating active matches.

### Terraform Plans

`.github/workflows/terraform-regions.yml` produces `-refresh=false` plans in empty runner workspaces. These are configuration-shape checks, not live drift plans, and cannot be applied.

The current regional Terraform state is local. GitHub-hosted `apply` or `destroy` would be unsafe because the runner cannot see the state used by `up.sh`, `down.sh`, `up-region.sh`, and `down-region.sh`. Approval-gated Terraform changes require a separate project phase:

1. create a versioned, access-controlled GCS state bucket
2. add a Terraform backend configuration
3. back up and explicitly migrate each regional workspace
4. verify local scripts and Actions use the same backend
5. add a separate Terraform WIF service account and protected environments

No state migration is automatic.

## Local Infrastructure Lifecycle

Local scripts remain the primary cost-control interface:

```bash
./scripts/up.sh
./scripts/down.sh

./scripts/up-region.sh europe
./scripts/down-region.sh europe

./scripts/up-region.sh north-america
./scripts/down-region.sh north-america
```

`up.sh/down.sh` exclusively own the primary South America control-plane environment. The regional scripts intentionally accept only Europe and North America and never deploy duplicate backend, frontend, PostgreSQL, or observability workloads.

## GitHub OIDC and WIF

GitHub deployments use short-lived OIDC credentials through Google Cloud Workload Identity Federation. No service-account JSON key is stored in GitHub.

The following is a one-time, explicitly approved cloud setup. Adjust identifiers if the repository owner/name changes:

```bash
export GCP_PROJECT_ID="xonotic-gameserver"
export GITHUB_REPOSITORY="NFNV/kubernetes_xonotic_gameserver"
export WIF_POOL_ID="github-actions"
export WIF_PROVIDER_ID="xonotic-repository"
export DEPLOY_SERVICE_ACCOUNT_NAME="github-xonotic-deployer"

export GCP_PROJECT_NUMBER="$(gcloud projects describe "${GCP_PROJECT_ID}" \
  --format='value(projectNumber)')"

gcloud iam workload-identity-pools create "${WIF_POOL_ID}" \
  --project "${GCP_PROJECT_ID}" \
  --location global \
  --display-name "GitHub Actions"

gcloud iam workload-identity-pools providers create-oidc "${WIF_PROVIDER_ID}" \
  --project "${GCP_PROJECT_ID}" \
  --location global \
  --workload-identity-pool "${WIF_POOL_ID}" \
  --issuer-uri "https://token.actions.githubusercontent.com" \
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition "assertion.repository=='${GITHUB_REPOSITORY}'"

gcloud iam service-accounts create "${DEPLOY_SERVICE_ACCOUNT_NAME}" \
  --project "${GCP_PROJECT_ID}" \
  --display-name "GitHub Xonotic deployer"

export DEPLOY_SERVICE_ACCOUNT="${DEPLOY_SERVICE_ACCOUNT_NAME}@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "${GCP_PROJECT_ID}" \
  --member "serviceAccount:${DEPLOY_SERVICE_ACCOUNT}" \
  --role roles/container.developer

gcloud iam service-accounts add-iam-policy-binding "${DEPLOY_SERVICE_ACCOUNT}" \
  --project "${GCP_PROJECT_ID}" \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL_ID}/attribute.repository/${GITHUB_REPOSITORY}"
```

`roles/container.developer` is the practical deployment boundary for this dev portfolio because the workflows reconcile Kubernetes workloads and RBAC in three temporary clusters. It does not grant Terraform permission to create or destroy GKE/network resources. A production environment should replace it with purpose-built Kubernetes RBAC and narrower Google IAM.

Terraform later needs a separate identity with permissions for GKE, node pools, Compute Engine firewall/network resources, Service Usage, and any state bucket. Do not attach those permissions to the normal deployment identity.

## Repository Configuration

Create these repository-level GitHub Variables:

| Variable | Value |
| --- | --- |
| `GCP_PROJECT_ID` | `xonotic-gameserver` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/<project-number>/locations/global/workloadIdentityPools/github-actions/providers/xonotic-repository` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `github-xonotic-deployer@xonotic-gameserver.iam.gserviceaccount.com` |
| `GCP_SA_CLUSTER_NAME` | `xonotic-mvp` |
| `GCP_SA_CLUSTER_ZONE` | `southamerica-west1-a` |
| `GCP_EU_CLUSTER_NAME` | `xonotic-eu` |
| `GCP_EU_CLUSTER_ZONE` | `europe-west1-b` |
| `GCP_NA_CLUSTER_NAME` | `xonotic-na` |
| `GCP_NA_CLUSTER_ZONE` | `us-central1-a` |

Create and protect these GitHub Environments before the first deployment:

- `control-plane-dev`
- `game-plane-south-america`
- `game-plane-europe`
- `game-plane-north-america`

Configure required reviewers on those environments. No GitHub Secret is required for GHCR because publication uses the repository `GITHUB_TOKEN`. Runtime application credentials remain Kubernetes Secrets created by the local lifecycle scripts.

Example setup with GitHub CLI:

```bash
gh variable set GCP_PROJECT_ID --body "xonotic-gameserver"
gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --body "projects/<project-number>/locations/global/workloadIdentityPools/github-actions/providers/xonotic-repository"
gh variable set GCP_DEPLOY_SERVICE_ACCOUNT --body "github-xonotic-deployer@xonotic-gameserver.iam.gserviceaccount.com"
gh variable set GCP_SA_CLUSTER_NAME --body "xonotic-mvp"
gh variable set GCP_SA_CLUSTER_ZONE --body "southamerica-west1-a"
gh variable set GCP_EU_CLUSTER_NAME --body "xonotic-eu"
gh variable set GCP_EU_CLUSTER_ZONE --body "europe-west1-b"
gh variable set GCP_NA_CLUSTER_NAME --body "xonotic-na"
gh variable set GCP_NA_CLUSTER_ZONE --body "us-central1-a"
```

## First Release

1. Configure WIF, the deploy service account, repository Variables, and protected Environments.
2. Bring up the desired infrastructure with the local scripts.
3. Merge through `master` CI and wait for **Publish Images** to record the release SHA and digests.
4. Deploy the control plane from GitHub Actions using the published full SHA.
5. Deploy the GameServer image to selected online regions using the same SHA.
6. Destroy temporary infrastructure locally when testing is complete.

GitHub CLI equivalents:

```bash
export RELEASE_SHA="$(git rev-parse HEAD)"

gh workflow run deploy-control-plane.yml -f image_sha="${RELEASE_SHA}"
gh workflow run deploy-game-plane.yml -f region=all -f image_sha="${RELEASE_SHA}"
gh workflow run terraform-regions.yml -f region=europe
```

## Rollback

Preferred control-plane rollback:

```bash
gh workflow run deploy-control-plane.yml -f image_sha="<previous-40-character-sha>"
```

Emergency Kubernetes rollback remains available while Deployment history exists:

```bash
kubectl rollout undo deployment/xonotic-allocator-backend -n xonotic-allocator-backend
kubectl rollout undo deployment/xonotic-allocator-frontend -n xonotic-allocator-backend
```

The preferred GameServer rollback is another regional workflow dispatch using the previous SHA. Ready GameServers roll to that image; already Allocated GameServers remain on their existing version until normal release.

## Safe Verification

Local checks do not contact or mutate GCP:

```bash
python -m compileall -q allocator-backend
npm --prefix allocator-frontend ci
npm --prefix allocator-frontend run build
terraform -chdir=infra fmt -check -recursive
terraform -chdir=infra init -backend=false
terraform -chdir=infra validate
bash -n scripts/*.sh
kubectl kustomize platform/allocator-backend/manifests >/dev/null
kubectl kustomize platform/allocator-frontend/manifests >/dev/null
kubectl kustomize platform/postgres/manifests >/dev/null
kubectl kustomize platform/agones/manifests >/dev/null
kubectl kustomize platform/observability >/dev/null
```

Safe workflow tests:

- open a pull request and inspect every CI job
- manually run a Terraform regional plan; confirm it says empty-runner configuration plan
- with a region deliberately offline, dispatch its game-plane workflow and confirm the not-provisioned summary
- inspect a published GHCR image and confirm both the `sha-...` tag and OCI revision label

Real deployments and all WIF/IAM commands remain explicit operator actions.
