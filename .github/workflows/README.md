# Workflows

GitHub Actions validates and packages the platform without requiring live GKE clusters. Deployment remains an explicit operation against infrastructure created through the local cost-control scripts.

- `ci.yml`: backend/frontend, Terraform, shell, workflow, manifest, observability, and pull-request image-build validation
- `publish-images.yml`: coordinated immutable backend/frontend/GameServer publication to GHCR after successful `master` CI
- `deploy-control-plane.yml`: manual immutable release to the South America control plane
- `deploy-game-plane.yml`: manual immutable Fleet update for South America, Europe, North America, or all provisioned regions
- `terraform-regions.yml`: non-applying regional configuration plans from empty GitHub runner state

Terraform apply/destroy remains local until the three workspace states are explicitly migrated to a shared remote backend. See [`docs/ci-cd.md`](../../docs/ci-cd.md) for setup, security boundaries, offline-cluster behavior, and rollback.
