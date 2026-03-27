# Project Notes

This file is the running context log for the repository. Update it over time so a future session can recover project state quickly.

## Current State

- Stage: Phase 1 infrastructure foundation and initial server container setup
- Status: minimal Terraform plus an initial Xonotic dedicated server image build context
- Goal: showcase platform engineering and DevOps practices using Xonotic dedicated servers as the workload

## Locked-In Context

- Game workload: Xonotic dedicated servers
- Cloud: Google Cloud Platform
- Kubernetes mode: GKE Standard
- Game server orchestration: Agones
- CI/CD: GitHub Actions
- GitHub to GCP auth: OIDC with Workload Identity Federation
- Container registry: GHCR
- Primary objective: demonstrate production-style platform engineering skills, not game modding

## Current Constraints

- Do not create Kubernetes manifests yet
- Keep the public repo lean; avoid separate architecture or ADR-heavy docs unless they become necessary again
- Prefer readable Terraform and Dockerfiles over abstraction or framework-heavy setup
- Keep IAM minimal until there is a concrete deployment or access requirement
- Keep the server image focused on the stock dedicated server path before adding orchestration-specific behavior

## Documentation Structure

- `README.md`: public-facing project overview, scope, concise architecture, roadmap, and brief rationale for major choices
- `PROJECT_NOTES.md`: deeper internal context, planning notes, and evolving constraints
- `infra/README.md`: explains the Terraform MVP foundation and how to run it
- `platform/README.md`: placeholder for cluster platform code area
- `server/README.md`: explains the dedicated server container setup, runtime assumptions, and local test needs
- `.gitignore`: practical defaults for local development noise, Terraform state, local env files, and generated artifacts

## Phase 1 Terraform Shape

- one zonal GKE Standard cluster
- one small node pool
- explicit node disk size and disk type
- default region and zone set for South America deployment (`southamerica-west1` / `southamerica-west1-a`)
- required GCP API enablement only
- no Artifact Registry resources because images will come from GHCR
- no GitHub OIDC or Workload Identity Federation resources yet
- no dedicated VPC yet; the MVP assumes the existing default network and subnetwork

## Initial Server Container Shape

- multi-stage Docker build using the official Xonotic release archive
- runtime image based on Debian slim with a non-root `xonotic` user
- baseline `server.cfg` stored in the repo
- runtime-generated `server.autoexec.cfg` for environment-driven overrides
- intended v1 image/runtime target is `linux/amd64`
- Apple Silicon local runs are smoke tests only
- no Agones, Kubernetes, or CI coupling yet

## Internal Planning Notes

- Keep architecture and decision detail summarized, not academic
- Public documentation should stay readable in one pass from the root README
- The strongest portfolio story is the end-to-end platform flow: GitHub -> OIDC/WIF -> GCP -> GKE Standard -> Agones -> Xonotic servers
- Initial implementation should continue to favor one cluster and one environment until there is a working baseline worth promoting
- Observability should be added later with a practical minimum: logs, metrics, alerts, and short runbooks
- If the default VPC assumption becomes a blocker, add dedicated networking in a later infra iteration rather than now
- The next server milestone should be proving the container starts locally and documenting the exact runtime dependencies and port behavior

## Expected Next Steps

- validate Terraform against a real GCP project
- build and test the Xonotic server image locally
- add remote state once the project moves beyond local-only iteration
- add minimal cluster access and deployment identity groundwork when GitHub delivery is introduced
- add initial Agones and platform deployment structure
- document observability and operations plan in more depth
