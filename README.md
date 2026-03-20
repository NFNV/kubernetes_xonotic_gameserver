# Xonotic Platform Engineering Demo

This repository is a production-style platform engineering project built around hosting Xonotic dedicated game servers on Kubernetes with Agones, running on Google Kubernetes Engine Standard. The point of the repo is to demonstrate infrastructure, platform, delivery, and operational thinking in a realistic but scoped way.

## Purpose

The project is intended to show how to design and incrementally deliver a game-server platform that looks like something a small platform team could own in production. It emphasizes clear architecture, staged rollout, secure cloud authentication, and practical tradeoffs over novelty.

## Scope

In scope:

- provisioning and managing a GKE Standard cluster on GCP
- using Agones to schedule and scale dedicated game servers
- packaging and operating the Xonotic server workload
- using GitHub Actions for CI/CD
- using GitHub OIDC with GCP Workload Identity Federation for keyless deployment auth
- documenting decisions, constraints, and delivery phases like a real engineering repository

## Non-Goals

Out of scope for the initial project direction:

- game modding or custom gameplay development
- building a general-purpose multiplayer backend
- supporting multiple clouds from day one
- supporting many environments before the base platform is stable
- premature optimization for massive scale before baseline operations are proven

## High-Level Architecture

At a high level, the repository is organized around four concerns:

- `infra/`: cloud foundation such as projects, networking, IAM, and cluster provisioning
- `platform/`: Kubernetes and Agones platform configuration that sits on top of the cluster
- `server/`: the Xonotic dedicated server packaging and runtime concerns
- `docs/`: architecture, decisions, and project-level documentation

Target runtime architecture:

1. GitHub Actions validates changes and deploys through GitHub OIDC to GCP.
2. GCP accepts federated identity via Workload Identity Federation rather than static keys.
3. GKE Standard provides the Kubernetes control plane and node pools.
4. Agones manages game server lifecycle on the cluster.
5. Xonotic dedicated server instances run as Agones-managed workloads.

This gives the project a practical split between infrastructure, platform, and workload layers, which is useful both for implementation and for explaining ownership boundaries.

## Phased Roadmap

### Phase 0: Repository Bootstrap

- establish repository layout
- document architecture, scope, and initial decisions
- define implementation boundaries

### Phase 1: Infrastructure Foundation

- introduce Terraform structure for GCP project resources, IAM, networking, and GKE
- define remote state and environment conventions
- wire GitHub OIDC to GCP Workload Identity Federation

### Phase 2: Platform Baseline

- add cluster bootstrap for namespaces, Agones, and shared platform components
- define rollout strategy for cluster-level changes
- document baseline operational procedures

### Phase 3: Workload Delivery

- package the Xonotic dedicated server workload
- define deployment approach through Agones
- document configuration boundaries between platform and workload

### Phase 4: CI/CD and Operations

- add validation, plan, and deployment workflows in GitHub Actions
- add policy, release, and rollback conventions
- begin structured observability and runbook documentation

### Phase 5: Hardening and Expansion

- improve reliability, scaling, and cost controls
- add richer metrics, logs, and alerts
- evaluate promotion to multiple environments if justified

## Repository Status

The repository is currently in a documentation-first bootstrap phase. Full Terraform, Docker, and Kubernetes implementation are intentionally deferred until the structure and decision record are in place.
