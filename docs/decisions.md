# Architecture Decision Record Log

This file captures the initial architecture decisions for the project. These decisions are intentionally practical and biased toward a strong first iteration rather than theoretical completeness.

## ADR-001: Use GKE Standard Instead of GKE Autopilot

### Status

Accepted

### Context

The project needs a managed Kubernetes control plane on GCP, but the workload is a dedicated game server platform where node shape, scheduling behavior, and workload placement can matter more than in standard stateless web applications.

### Decision

Use GKE Standard as the initial cluster mode.

### Rationale

- game server workloads often need tighter control over node pools and scheduling tradeoffs
- GKE Standard is easier to explain in a platform engineering portfolio because more of the cluster operating model is visible
- it leaves room for future experiments with taints, labels, node pool isolation, and cost/performance tuning

### Consequences

- more cluster management responsibility remains with the operator
- the repo can demonstrate more platform ownership instead of abstracting it away
- some operational complexity is accepted in exchange for flexibility

### Alternatives Considered

- GKE Autopilot: simpler operations, but less aligned with the goal of showing platform-level control and workload-aware cluster design

## ADR-002: Use Agones for Game Server Lifecycle Management

### Status

Accepted

### Context

Dedicated game servers have lifecycle patterns that are different from common web services. The platform needs a scheduler-aware way to manage server instances, readiness, allocation, and scaling semantics.

### Decision

Use Agones as the game server orchestration layer on Kubernetes.

### Rationale

- Agones is purpose-built for multiplayer dedicated server workloads
- it gives the project a credible platform story instead of forcing generic Kubernetes abstractions onto a game-server problem
- it keeps the focus on platform engineering rather than inventing custom controllers too early

### Consequences

- the platform layer depends on an additional controller and CRD set
- the project inherits Agones-specific concepts that must be learned and documented
- in return, the design better matches the real workload

### Alternatives Considered

- plain Kubernetes Deployments or StatefulSets: workable for a demo, but weaker in lifecycle semantics for game servers
- custom controllers from day one: too much implementation cost for the current project phase

## ADR-003: Use GitHub Actions as the Initial CI/CD System

### Status

Accepted

### Context

The source code is hosted in GitHub, and the project needs a practical CI/CD system that is easy to adopt, visible to reviewers, and sufficient for repository validation plus deployment automation.

### Decision

Use GitHub Actions for CI/CD.

### Rationale

- tight integration with GitHub reduces setup friction
- workflow files in-repo make the delivery story easy to inspect
- GitHub Actions is common enough to be immediately legible to most engineering reviewers

### Consequences

- the project accepts GitHub-centric pipeline design initially
- advanced enterprise delivery features may require more design later
- the implementation path is faster and easier to understand for a portfolio project

### Alternatives Considered

- Cloud Build: stronger GCP alignment, but weaker fit with GitHub-centered repository operations
- self-hosted CI platforms: more control, but unnecessary complexity for the initial phase

## ADR-004: Use GitHub OIDC with GCP Workload Identity Federation Instead of Service Account Keys

### Status

Accepted

### Context

CI/CD needs to authenticate from GitHub to GCP. A straightforward but weak pattern would be storing long-lived service account keys in repository secrets.

### Decision

Use GitHub OIDC with GCP Workload Identity Federation for deployment authentication.

### Rationale

- avoids long-lived static credentials
- aligns with modern cloud security practice
- produces a stronger platform engineering story by showing secure identity federation instead of key distribution

### Consequences

- the initial setup is more complex than dropping a JSON key into secrets
- IAM and trust relationships need clearer documentation
- the resulting security posture is materially better and more defensible

### Alternatives Considered

- service account keys in GitHub secrets: easier to bootstrap, but a weaker and less modern security pattern

## ADR-005: Start with One Cluster and One Environment

### Status

Accepted

### Context

It is tempting to design for dev, staging, and production from the beginning, but that increases infrastructure, CI/CD, and operational complexity before the baseline platform has proven value.

### Decision

Start with one cluster and one environment.

### Rationale

- keeps the project small enough to finish credibly
- avoids multiplying complexity before workflows, platform conventions, and workload behavior are understood
- makes it easier to explain the core design clearly to reviewers

### Consequences

- environment promotion and separation are deferred
- some future restructuring may be required when expanding
- the initial delivery path is much more practical and focused

### Alternatives Considered

- multiple environments from day one: closer to enterprise shape, but too much early complexity for the current goals
