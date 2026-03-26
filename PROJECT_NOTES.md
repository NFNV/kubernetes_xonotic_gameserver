# Project Notes

This file is the running context log for the repository. Update it over time so a future session can recover project state quickly.

## Current State

- Stage: Phase 1 infrastructure foundation started
- Status: minimal Terraform added for a single-environment GCP and GKE MVP
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

- Do not create Dockerfiles yet
- Do not create Kubernetes manifests yet
- Keep the public repo lean; avoid separate architecture or ADR-heavy docs unless they become necessary again
- Prefer readable Terraform over abstraction or module indirection
- Keep IAM minimal until there is a concrete deployment or access requirement

## Documentation Structure

- `README.md`: public-facing project overview, scope, concise architecture, roadmap, and brief rationale for major choices
- `PROJECT_NOTES.md`: deeper internal context, planning notes, and evolving constraints
- `infra/README.md`: explains the Terraform MVP foundation and how to run it
- `platform/README.md`: placeholder for cluster platform code area
- `server/README.md`: placeholder for workload/server packaging area
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

## Internal Planning Notes

- Keep architecture and decision detail summarized, not academic
- Public documentation should stay readable in one pass from the root README
- The strongest portfolio story is the end-to-end platform flow: GitHub -> OIDC/WIF -> GCP -> GKE Standard -> Agones -> Xonotic servers
- Initial implementation should continue to favor one cluster and one environment until there is a working baseline worth promoting
- Observability should be added later with a practical minimum: logs, metrics, alerts, and short runbooks
- If the default VPC assumption becomes a blocker, add dedicated networking in a later infra iteration rather than now

## Expected Next Steps

- validate Terraform against a real GCP project
- add remote state once the project moves beyond local-only iteration
- add minimal cluster access and deployment identity groundwork when GitHub delivery is introduced
- add initial Agones and platform deployment structure
- document observability and operations plan in more depth
