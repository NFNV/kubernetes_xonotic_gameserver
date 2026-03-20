# Architecture

This document describes the initial target architecture for the Xonotic platform engineering demo. It is intentionally high level at this stage so the repository can establish system boundaries before implementation details are added.

## Components

### Source Control and Delivery

- GitHub hosts the source repository and pull request workflow.
- GitHub Actions runs validation, planning, and deployment pipelines.
- GitHub OIDC federates identity to GCP through Workload Identity Federation.

### Cloud Foundation

- GCP provides the cloud environment.
- GKE Standard hosts the Kubernetes cluster.
- GCP IAM controls access for humans, CI/CD, and cluster-integrated services.
- Networking, service accounts, and project-level guardrails will live in the infrastructure layer.

### Platform Layer

- Kubernetes provides the execution substrate.
- Agones manages dedicated game server lifecycle, allocation, and scaling patterns specific to multiplayer workloads.
- Shared platform concerns such as namespaces, policies, and supporting controllers will live in the platform layer.

### Workload Layer

- Xonotic dedicated server instances are the primary workload.
- Server image, runtime configuration, and game-specific operational notes will live in the server layer.
- The workload will be modeled as an Agones-managed game server rather than a generic deployment.

## Request and Traffic Flow

The expected runtime flow is:

1. A player or game client targets a reachable game server endpoint.
2. Traffic enters GCP networking and reaches the Kubernetes worker nodes that host Agones-managed game server pods.
3. Agones provides lifecycle control for the server process, including scheduling and state transitions appropriate for dedicated game sessions.
4. The Xonotic server process handles gameplay traffic directly for the session.

Important tradeoff:

- This project is centered on dedicated server hosting, not on building a custom matchmaking or session backend. Traffic flow is therefore intentionally simpler than a full commercial multiplayer platform.

## CI/CD Flow

The expected delivery flow is:

1. An engineer pushes changes or opens a pull request in GitHub.
2. GitHub Actions runs repository checks appropriate to the change type.
3. For deploy-capable workflows, GitHub exchanges its OIDC identity for short-lived GCP credentials through Workload Identity Federation.
4. The workflow applies infrastructure or platform changes to GCP and GKE based on repository ownership boundaries.
5. Deployment results, logs, and artifacts remain attached to the GitHub workflow run for traceability.

Initial design intent:

- keep delivery logic in GitHub Actions because it is close to the source of truth
- avoid long-lived cloud keys in repository secrets
- separate infrastructure changes from platform and workload changes as the repo matures

## Future Observability Plan

Observability is not implemented yet, but the intended direction is:

- logs: centralize container and platform logs for cluster and workload troubleshooting
- metrics: collect cluster, node, pod, and Agones-specific health indicators
- alerts: define actionable alerts around server availability, crash loops, node pressure, and deployment failures
- dashboards: maintain a small set of operator-focused dashboards for platform health and game server capacity
- runbooks: document routine operational tasks and failure response steps in the repo

The likely practical path is to start with managed or low-friction GCP-native capabilities and then decide whether additional tooling is justified by complexity.

## Assumptions and Constraints

### Assumptions

- a single GCP project and a single primary cluster are enough to demonstrate the platform design initially
- GKE Standard is preferred because node pool control and scheduling behavior matter for game server workloads
- Agones is the right abstraction for dedicated game server lifecycle management
- GitHub remains the source of truth for both application and infrastructure delivery

### Constraints

- the project is optimized for clarity and demonstrable engineering practice, not for maximum feature breadth
- the repository should remain understandable to a recruiter or hiring manager reviewing it quickly
- implementation will be phased to avoid mixing architecture exploration with premature build-out
- initial design should leave room for later additions such as separate environments, richer observability, and stronger policy controls
