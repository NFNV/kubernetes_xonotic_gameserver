# Agones Phase 1

This directory contains the first Agones-based replacement for the plain Kubernetes connectivity checkpoint.

Phase 1 is intentionally narrow:

- install Agones on the existing GKE cluster
- deploy one Xonotic `GameServer`
- confirm the `GameServer` reaches `Ready`
- connect a real client to the Agones-managed server

It intentionally does not add yet:

- `Fleet`
- `GameServerAllocation`
- allocator service
- autoscaling
- Agones SDK integration beyond the minimal `Ready` hook used in this phase

The plain Kubernetes checkpoint in `platform/connectivity-checkpoint/` remains the fallback reference while this phase is being validated.

## Files

- `manifests/namespace.yaml`: namespace for the first Xonotic Agones resources
- `manifests/xonotic-gameserver.yaml`: one explicit `GameServer` resource for the Xonotic server

## Why This Phase Uses One GameServer

The goal here is not to introduce the whole Agones control model at once. It is to swap the working Xonotic server from a plain Kubernetes `Deployment` to a single Agones `GameServer` while keeping the runtime configuration as close as possible to the already-proven checkpoint.

That gives us one new moving part at a time:

1. Agones controller installation
2. Agones `GameServer` lifecycle
3. direct client connectivity to the Agones-managed server

## Image Assumption

Before applying the Agones `GameServer`, republish the server image from this repo revision so the image includes the minimal Agones `Ready` hook now present in `server/entrypoint.sh`.

The simplest path is to rerun the existing GitHub Actions workflow and refresh:

- `ghcr.io/nfnv/xonotic-server:connectivity-checkpoint`

## Install Agones

Use the official Agones Helm chart pattern. For this first phase, keep the install tight:

- install into `agones-system`
- disable the Agones ping service because this repo already has a working manual connectivity path and does not need the extra sample `LoadBalancer`
- watch only the Xonotic game server namespace for now

Create the game server namespace first:

```bash
kubectl apply -f platform/agones/manifests/namespace.yaml
```

Add the official Helm repo and install or update Agones:

```bash
helm repo add agones https://agones.dev/chart/stable
helm repo update
helm upgrade --install agones agones/agones \
  --namespace agones-system \
  --create-namespace \
  --set agones.ping.install=false \
  --set "gameservers.namespaces={xonotic-agones}"
```

Confirm the Agones controllers are running:

```bash
kubectl get pods -n agones-system
kubectl get crd | grep agones.dev
```

## Apply The First GameServer

Apply the single `GameServer` manifest:

```bash
kubectl apply -f platform/agones/manifests/xonotic-gameserver.yaml
```

Inspect it:

```bash
kubectl get gameserver -n xonotic-agones
kubectl describe gameserver xonotic-gameserver -n xonotic-agones
kubectl logs xonotic-gameserver -n xonotic-agones -c server --tail=100
```

Success for this phase starts with the `GameServer` reaching `Ready`.

## Get The Client Address And Port

For this first phase, the `GameServer` uses a static UDP host port to stay close to the working checkpoint.

Get the connection target:

```bash
kubectl get gameserver xonotic-gameserver -n xonotic-agones -o jsonpath='{.status.address}:{.status.ports[0].port}{"\n"}'
```

You can also inspect it in a wider table:

```bash
kubectl get gameserver -n xonotic-agones -o wide
```

## Validate Client Connectivity

Connect a real Xonotic client to the printed address and port:

```text
connect <gameserver-address>:26000
```

Success criteria:

- the `GameServer` reaches `Ready`
- `kubectl get gameserver -n xonotic-agones` shows the node address and port
- the client joins the server
- the server logs show the incoming client connection

## GKE And Firewall Assumptions

This Agones phase does not use a `LoadBalancer` Service. The `GameServer` is exposed through the node address and Agones-managed host port.

Assumptions for this to work on GKE:

- the node that runs the `GameServer` has a reachable external address for your test path
- UDP `26000` is allowed to that node by GCP firewall policy
- a single `GameServer` on static UDP `26000` is acceptable for this phase

This repo now handles that phase-1 requirement with one narrow Terraform-managed VPC firewall rule for UDP `26000`. That is intentionally narrower than the later production shape.

## What Comes Next

### Phase 2 Later: Fleet

After one `GameServer` is proven, the next Agones step is a `Fleet` so Agones manages a repeatable pool instead of one named resource.

### Phase 3 Later: Allocation And Scaling

Only after the Fleet path is working should this repo add:

- `GameServerAllocation`
- allocator service
- Fleet autoscaling
