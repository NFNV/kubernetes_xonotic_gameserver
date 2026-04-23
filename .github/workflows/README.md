# Workflows

This directory contains GitHub Actions workflow definitions for the smallest practical delivery steps in the project.

Current workflow:

- `publish-server-image.yml`: manually builds the Xonotic dedicated server image for `linux/amd64` and pushes it to GHCR using the repository `GITHUB_TOKEN`
- `publish-allocator-backend-image.yml`: manually or automatically builds the in-cluster allocator backend image for `linux/amd64` and pushes it to GHCR using the repository `GITHUB_TOKEN`
- `publish-allocator-frontend-image.yml`: manually or automatically builds the allocator admin frontend image for `linux/amd64` and pushes it to GHCR using the repository `GITHUB_TOKEN`
