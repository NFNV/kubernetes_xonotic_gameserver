# Platform Layer

This directory is reserved for cluster-level platform components that sit on top of the base infrastructure.

`connectivity-checkpoint/` remains the smallest pre-Agones deployment path for proving direct client connectivity on GKE.

`agones/` now contains the first Agones phase: installation guidance plus one `GameServer` manifest that replaces the plain Kubernetes checkpoint without yet introducing Fleets or allocation.

Expected future contents:

- the pre-Agones connectivity checkpoint documentation and manifests
- Agones installation and configuration
- namespaces and shared Kubernetes resources
- cluster policies and supporting controllers
- platform rollout documentation
