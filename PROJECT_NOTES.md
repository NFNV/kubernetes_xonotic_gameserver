# Project Notes

This file is the running context log for the repository. Update it over time so a future session can recover project state quickly.

## Current State

- Stage: Phase 2 initial Agones baseline
- Status: Terraform has been applied successfully, the GKE Standard cluster exists, `kubectl` access works, the Xonotic server image has been published to GHCR, the plain Kubernetes connectivity checkpoint has passed, and the repo now includes the first Agones installation and single-GameServer path for validation on the existing cluster
- Goal: validate one Agones-managed Xonotic `GameServer` before adding Fleets or allocation workflows

## Locked-In Context

- Game workload: Xonotic dedicated servers
- Cloud: Google Cloud Platform
- Kubernetes mode: GKE Standard
- Game server orchestration: Agones
- CI/CD: GitHub Actions
- GitHub to GCP auth: OIDC with Workload Identity Federation
- Container registry: GHCR
- Primary objective: demonstrate production-style platform engineering skills, not game modding
- Current proof strategy before Agones: one public registry image, one Kubernetes Deployment replica, one UDP `LoadBalancer` Service with `externalTrafficPolicy: Local`, and direct client connect by IP and port

## Current Constraints

- Allow only the minimum Kubernetes manifests required for the cloud connectivity checkpoint
- Keep this checkpoint separate from the later Agones design
- Keep the public repo lean; avoid separate architecture or ADR-heavy docs unless they become necessary again
- Prefer readable Terraform and Dockerfiles over abstraction or framework-heavy setup
- Keep IAM minimal until there is a concrete deployment or access requirement
- Keep the server image focused on the stock dedicated server path before adding orchestration-specific behavior
- Use a public GHCR image for this checkpoint so Kubernetes deployment stays free of image pull secret work

## Documentation Structure

- `README.md`: public-facing project overview, scope, concise architecture, roadmap, and brief rationale for major choices
- `PROJECT_NOTES.md`: deeper internal context, planning notes, and evolving constraints
- `infra/README.md`: explains the Terraform MVP foundation and how to run it
- `platform/README.md`: explains the platform area and the limited pre-Agones checkpoint exception
- `platform/connectivity-checkpoint/README.md`: exact GHCR publish, deployment, and real-client connectivity test steps for the one-server GKE proof
- `platform/agones/README.md`: first-phase Agones installation, one-GameServer deployment, and validation flow
- `server/README.md`: explains the dedicated server container setup, runtime assumptions, and local test needs
- `scripts/up.sh` and `scripts/down.sh`: local operator scripts for low-cost bring-up and teardown of the Terraform-backed GKE checkpoint
- `scripts/env.sh.example`: template for project-local operator environment variables loaded by the local scripts
- `.github/workflows/publish-server-image.yml`: manual GHCR publish workflow for the server image
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
- no full Agones SDK integration yet; only a minimal phase-1 `Ready` hook is added so one `GameServer` can reach `Ready`

## Internal Planning Notes

- Keep architecture and decision detail summarized, not academic
- Public documentation should stay readable in one pass from the root README
- The strongest portfolio story is the end-to-end platform flow: GitHub -> OIDC/WIF -> GCP -> GKE Standard -> Agones -> Xonotic servers
- Initial implementation should continue to favor one cluster and one environment until there is a working baseline worth promoting
- The plain Kubernetes checkpoint is now validated, so future work can treat the image, UDP port, and basic GKE exposure path as a known-good baseline
- The checkpoint used the least ambiguous networking path rather than the eventual long-term production exposure model
- The first Agones phase should stay limited to controller installation plus one `GameServer`; Fleets, allocation, and scaling come later
- Distinguish clearly between infrastructure that is implemented in Terraform and infrastructure that has actually been applied in a real GCP project
- Observability should be added later with a practical minimum: logs, metrics, alerts, and short runbooks
- If the default VPC assumption becomes a blocker, add dedicated networking in a later infra iteration rather than now
- The current platform milestone is validating one Agones-managed Xonotic server on top of the already-proven connectivity baseline

## Expected Next Steps

- republish the Xonotic server image so the GHCR tag includes the phase-1 Agones `Ready` hook
- install Agones on the existing GKE cluster
- deploy the single Xonotic `GameServer` and validate `Ready` state plus client connectivity
- add a first `Fleet` only after the single `GameServer` path is proven
- add remote state once the project moves beyond local-only iteration
- add minimal cluster access and deployment identity groundwork when GitHub delivery is introduced
- document observability and operations plan in more depth
