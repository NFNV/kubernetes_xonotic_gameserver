# Workflows

This directory contains GitHub Actions workflow definitions for the smallest practical delivery steps in the project.

Current workflow:

- `publish-server-image.yml`: manually builds the Xonotic dedicated server image for `linux/amd64` and pushes it to GHCR using the repository `GITHUB_TOKEN`
