# Project Notes

This file is the running context log for the repository. Update it over time so a future session can recover project state quickly.

## Current State

- Stage: repository bootstrap
- Status: lean public documentation and structure only
- Goal: showcase platform engineering and DevOps practices using Xonotic dedicated servers as the workload

## Locked-In Context

- Game workload: Xonotic dedicated servers
- Cloud: Google Cloud Platform
- Kubernetes mode: GKE Standard
- Game server orchestration: Agones
- CI/CD: GitHub Actions
- GitHub to GCP auth: OIDC with Workload Identity Federation
- Primary objective: demonstrate production-style platform engineering skills, not game modding

## Current Constraints

- Do not create full Terraform yet
- Do not create Dockerfiles yet
- Do not create Kubernetes manifests yet
- Favor practical documentation, explicit tradeoffs, and phased delivery
- Keep the public repo lean; avoid separate architecture or ADR-heavy docs unless they become necessary again

## Documentation Structure

- `README.md`: public-facing project overview, scope, concise architecture, roadmap, and brief rationale for major choices
- `PROJECT_NOTES.md`: deeper internal context, planning notes, and evolving constraints
- `infra/README.md`: placeholder for infrastructure code area
- `platform/README.md`: placeholder for cluster platform code area
- `server/README.md`: placeholder for workload/server packaging area
- `.gitignore`: practical defaults for local development noise, Terraform state, local env files, and generated artifacts

## Internal Planning Notes

- Keep architecture and decision detail summarized, not academic
- Public documentation should stay readable in one pass from the root README
- The strongest portfolio story is the end-to-end platform flow: GitHub -> OIDC/WIF -> GCP -> GKE Standard -> Agones -> Xonotic servers
- Initial implementation should continue to favor one cluster and one environment until there is a working baseline worth promoting
- Observability should be added later with a practical minimum: logs, metrics, alerts, and short runbooks

## Expected Next Steps

- define repository conventions and naming
- add initial Terraform structure for GCP and GKE
- add initial Agones and platform deployment structure
- add first GitHub Actions workflow skeletons
- document observability and operations plan in more depth
