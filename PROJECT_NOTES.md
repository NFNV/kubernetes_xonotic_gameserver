# Project Notes

This file is the running context log for the repository. Update it over time so a future session can recover project state quickly.

## Current State

- Stage: Phase 1.5 cloud connectivity checkpoint before Agones
- Status: minimal Terraform for the GCP/GKE foundation is implemented in code, the initial Xonotic dedicated server image build context exists, and a pre-Agones single-server GKE deployment path is documented for real client connectivity validation
- Goal: prove real client UDP connectivity to one Xonotic server in GKE before continuing into broader platform buildout

## Locked-In Context

- Game workload: Xonotic dedicated servers
- Cloud: Google Cloud Platform
- Kubernetes mode: GKE Standard
- Game server orchestration: Agones
- CI/CD: GitHub Actions
- GitHub to GCP auth: OIDC with Workload Identity Federation
- Container registry: GHCR
- Primary objective: demonstrate production-style platform engineering skills, not game modding
- Current proof strategy before Agones: one public registry image, one Kubernetes Deployment replica, one UDP `LoadBalancer` Service, and direct client connect by IP and port

## Current Constraints

- Allow only the minimum Kubernetes manifests required for the cloud connectivity checkpoint
- Keep this checkpoint separate from the later Agones design
- Keep the public repo lean; avoid separate architecture or ADR-heavy docs unless they become necessary again
- Prefer readable Terraform and Dockerfiles over abstraction or framework-heavy setup
- Keep IAM minimal until there is a concrete deployment or access requirement
- Keep the server image focused on the stock dedicated server path before adding orchestration-specific behavior

## Documentation Structure

- `README.md`: public-facing project overview, scope, concise architecture, roadmap, and brief rationale for major choices
- `PROJECT_NOTES.md`: deeper internal context, planning notes, and evolving constraints
- `infra/README.md`: explains the Terraform MVP foundation and how to run it
- `platform/README.md`: explains the platform area and the limited pre-Agones checkpoint exception
- `platform/connectivity-checkpoint/README.md`: exact deployment and real-client connectivity test steps for the one-server GKE proof
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
- The immediate gating milestone is no longer local container startup; it is a real client successfully joining a GKE-hosted server over UDP
- For this checkpoint, prefer the least ambiguous networking path even if it is not the long-term production exposure model
- Distinguish clearly between infrastructure that is implemented in Terraform and infrastructure that has actually been applied in a real GCP project
- Observability should be added later with a practical minimum: logs, metrics, alerts, and short runbooks
- If the default VPC assumption becomes a blocker, add dedicated networking in a later infra iteration rather than now
- The next platform milestone after this checkpoint is Agones integration only if cloud connectivity is proven

## Expected Next Steps

- validate Terraform against a real GCP project
- create the GKE cluster and node pool by applying the existing Terraform
- fetch kubeconfig credentials only after Terraform apply completes
- build and publish the Xonotic server image as a public `linux/amd64` image for GKE to pull
- deploy the one-server connectivity checkpoint manifests to GKE
- test direct real-client join over the GKE load balancer IP and UDP port
- add remote state once the project moves beyond local-only iteration
- add minimal cluster access and deployment identity groundwork when GitHub delivery is introduced
- add initial Agones and broader platform deployment structure only after connectivity is confirmed
- document observability and operations plan in more depth
