# Xonotic Platform Engineering Demo

This repository is a practical platform engineering project built around running Xonotic dedicated servers on Kubernetes with Agones on GCP. The goal is to show production-style infrastructure, platform, delivery, and operational thinking in a repo that stays small enough to finish and easy enough to review quickly.

Before moving into the full platform path, the project now includes a narrow cloud connectivity checkpoint: deploy one Xonotic dedicated server to GKE and prove that a real client can join over UDP from outside the cluster.

## Purpose

Use a real game-server workload to demonstrate:

- infrastructure and platform design on GCP
- Kubernetes operations with Agones
- secure CI/CD from GitHub Actions to GCP using OIDC and Workload Identity Federation
- clear ownership boundaries between infrastructure, platform, and workload layers

## Scope

In scope:

- GKE Standard cluster provisioning and baseline cloud setup
- Agones as the game-server orchestration layer
- Xonotic dedicated server packaging and deployment
- GitHub Actions for validation and deployment workflows
- practical documentation that explains tradeoffs without turning into a documentation-heavy repo

## Non-Goals

Out of scope for the initial version:

- game modding or custom gameplay work
- building a full matchmaking or multiplayer backend
- multi-cloud support
- multiple environments before the core path works
- deep enterprise process before there is a working platform baseline

## Architecture Overview

The planned runtime path is straightforward:

1. GitHub Actions validates and deploys changes.
2. GitHub authenticates to GCP through OIDC and Workload Identity Federation.
3. GKE Standard runs the cluster and node pools.
4. Agones manages dedicated game server lifecycle on the cluster.
5. Xonotic dedicated servers run as the primary workload.

Repository layout:

- `infra/`: GCP, IAM, networking, and cluster provisioning
- `platform/`: cluster-level components such as the pre-Agones connectivity checkpoint and later shared Kubernetes configuration
- `server/`: Xonotic workload packaging and runtime concerns

## Why These Choices

- `GKE Standard`: more control over node pools and scheduling than Autopilot, which is useful for game server workloads and better for demonstrating platform ownership
- `Agones`: purpose-built for dedicated game server lifecycle management, which is a better fit than forcing generic Kubernetes primitives to do all the work
- `GitHub Actions`: close to the repo, easy to review, and enough for the CI/CD needs of this project
- `OIDC + Workload Identity Federation`: avoids long-lived service account keys and reflects a more defensible cloud auth pattern
- `One cluster, one environment`: keeps the project focused and shippable before adding environment sprawl

## Roadmap

### Phase 0: Bootstrap

- establish repo structure and conventions
- document the target shape and delivery boundaries

### Phase 1: Infrastructure Foundation

- add Terraform structure for GCP, IAM, networking, and GKE
- configure GitHub to GCP authentication with Workload Identity Federation

### Phase 1.5: Cloud Connectivity Checkpoint

- publish the Xonotic dedicated server image to a pullable registry
- run exactly one server on GKE
- expose it with the simplest practical UDP path
- verify that a real client can join before adding Agones

### Phase 2: Platform Baseline

- add Agones and shared cluster-level components
- define the first Fleet-and-allocation workflow before autoscaling

### Phase 3: Workload Delivery

- package and deploy the Xonotic dedicated server workload
- separate workload concerns cleanly from infrastructure and platform concerns

### Phase 4: CI/CD and Hardening

- add validation, plan, and deploy workflows
- improve observability, reliability, and operational clarity

## Current Status

This repository is past the plain Kubernetes connectivity checkpoint and the first single-`GameServer` Agones step. Terraform has been applied, the GKE Standard cluster exists, the Xonotic server image has been published to GHCR, and the repo now includes the next Agones phase: a small Fleet plus a basic `GameServerAllocation` flow, while keeping the earlier checkpoint and single-`GameServer` path as reference points.
