# Platform Layer

This directory is reserved for cluster-level platform components that sit on top of the base infrastructure.

One exception exists during bootstrap: `connectivity-checkpoint/` contains the smallest possible pre-Agones deployment path for proving that a real Xonotic client can join a server running on GKE over UDP.

Expected future contents:

- the pre-Agones connectivity checkpoint documentation and manifests
- Agones installation and configuration
- namespaces and shared Kubernetes resources
- cluster policies and supporting controllers
- platform rollout documentation

Anything beyond the checkpoint should wait until cloud connectivity is proven.
