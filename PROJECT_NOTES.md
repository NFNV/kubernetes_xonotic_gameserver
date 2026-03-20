# Project Notes

This file is the running context log for the repository. Update it over time so a future session can recover project state quickly.

## Current State

- Stage: repository bootstrap
- Status: documentation and structure only
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

## Initial Documentation Set

- `README.md`: project purpose, scope, non-goals, architecture, roadmap
- `docs/architecture.md`: system and delivery architecture
- `docs/decisions.md`: ADR-style rationale for initial choices
- `infra/README.md`: placeholder for infrastructure code area
- `platform/README.md`: placeholder for cluster platform code area
- `server/README.md`: placeholder for workload/server packaging area
- `.gitignore`: practical defaults for local development noise, Terraform state, local env files, and generated artifacts

## Expected Next Steps

- define repository conventions and naming
- add initial Terraform structure for GCP and GKE
- add initial Agones and platform deployment structure
- add first GitHub Actions workflow skeletons
- document observability and operations plan in more depth
