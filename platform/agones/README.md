# Agones Phases

This directory now contains the Agones migration path for the project.

The plain Kubernetes checkpoint under `platform/connectivity-checkpoint/` remains the fallback reference for the pre-Agones networking model.

## Phase 1 Reference: Single GameServer

The first Agones step in this repo keeps one explicit `GameServer` as the smallest control-plane replacement for the plain Kubernetes checkpoint.

Reference manifest:

- `manifests/xonotic-gameserver.yaml`

That phase uses:

- one `GameServer`
- `portPolicy: Static`
- `hostPort: 26000`
- direct client connect to one node IP and one fixed UDP port

This file stays in the repo as a reference and fallback while the project moves to Fleet-based allocation.

## Phase Now: Fleet Plus Allocation

The current Agones phase replaces the one-off `GameServer` checkpoint with:

- one small `Fleet`
- one test `GameServerAllocation` flow

This is the right next step before autoscaling because it introduces the core Agones serving model without adding more systems than necessary:

1. keep a warm pool of ready game servers
2. allocate one game server atomically when a match needs one
3. inspect the returned address and port
4. connect the client directly to that allocated server

It intentionally does not add yet:

- `FleetAutoscaler`
- allocator service
- backend or frontend matchmaking components

## What A Fleet Is

A `Fleet` is a managed set of warm `GameServer` instances. Instead of creating one named `GameServer` by hand, Agones maintains a desired pool size for you.

For this phase, the Fleet stays deliberately small:

- `replicas: 2`

That is enough to prove that Agones can keep multiple ready Xonotic servers around and allocate one on demand.

## What GameServerAllocation Does

A `GameServerAllocation` asks Agones to atomically pick one `Ready` game server that matches the selector and mark it `Allocated`.

That matters because the connection target is no longer a single fixed server:

- the allocation picks one warm server from the Fleet
- Agones returns the server address and the actual external port to use
- the client connects to that specific address and port

## Why Static hostPort 26000 Is Not Right For A Fleet

The single-`GameServer` phase used:

- `portPolicy: Static`
- `hostPort: 26000`

That works for one server, but it is not appropriate for a Fleet of two servers on one node. A node cannot bind multiple Pods to the same UDP `hostPort` at the same time.

For a Fleet, this repo switches to:

- `portPolicy: Dynamic`
- `containerPort: 26000`
- dynamically assigned `hostPort` values from a small Agones port range

The Xonotic server still listens on `26000` inside the container. Agones maps each Fleet instance to a different external node port, and the client must connect to the allocated server's returned host port, not assume `26000`.

## Dynamic Port Range For This Phase

To keep this phase explicit and reviewable, Agones should be installed with a narrow dynamic port range:

- `7000-7010`

That is large enough for this dev cluster phase and much cleaner than opening the default `7000-8000` range.

The current Agones-aware `scripts/up.sh` installs or upgrades Agones with:

- `gameservers.minPort=7000`
- `gameservers.maxPort=7010`

## Files

- `manifests/namespace.yaml`: namespace for Xonotic Agones resources
- `manifests/xonotic-gameserver.yaml`: single-GameServer reference from phase 1
- `manifests/xonotic-fleet.yaml`: current Fleet manifest with `replicas: 2`
- `manifests/xonotic-gameserverallocation.yaml`: test allocation manifest using `generateName`

## Install Or Upgrade Agones For This Phase

Create the namespace first:

```bash
kubectl apply -f platform/agones/manifests/namespace.yaml
```

Install or upgrade Agones with the explicit namespace watch and dynamic port range:

```bash
helm repo add agones https://agones.dev/chart/stable --force-update
helm repo update
helm upgrade --install agones agones/agones \
  --namespace agones-system \
  --create-namespace \
  --set agones.ping.install=false \
  --set gameservers.minPort=7000 \
  --set gameservers.maxPort=7010 \
  --set "gameservers.namespaces={xonotic-agones}"
```

Confirm the controllers are healthy:

```bash
kubectl get pods -n agones-system
```

## Apply The Fleet

Apply the Fleet:

```bash
kubectl apply -f platform/agones/manifests/xonotic-fleet.yaml
```

Watch Fleet and `GameServer` state:

```bash
kubectl get fleet -n xonotic-agones -w
kubectl get gameserver -n xonotic-agones -w
```

Inspect the Fleet in more detail:

```bash
kubectl describe fleet xonotic-fleet -n xonotic-agones
kubectl get gameserver -n xonotic-agones -o wide
```

Success for this step means:

- the Fleet reaches `replicas: 2`
- both backing `GameServer` instances become `Ready`

## Create A Test Allocation

Use `kubectl create`, not `kubectl apply`, because the allocation manifest uses `generateName` and each test allocation should create a fresh object:

```bash
allocation_name="$(kubectl create -f platform/agones/manifests/xonotic-gameserverallocation.yaml -o name)"
echo "${allocation_name}"
```

Inspect the allocation:

```bash
kubectl get gameserverallocation -n xonotic-agones
kubectl get "${allocation_name}" -n xonotic-agones -o yaml
kubectl get "${allocation_name}" -n xonotic-agones -o jsonpath='{.status.address}:{.status.ports[0].port}{"\n"}'
```

You can also confirm which Fleet server was allocated:

```bash
kubectl get gameserver -n xonotic-agones
```

One server should move from `Ready` to `Allocated`.

## Test Client Connectivity

Connect the Xonotic client to the allocated address and port:

```text
connect <allocated-address>:<allocated-port>
```

Do not assume the Fleet connection port is `26000`. For the Fleet phase, the external connection port is dynamically assigned from the Agones range.

For server-side confirmation:

```bash
kubectl logs -n xonotic-agones -l app=xonotic-fleet -c server --tail=100
```

## Networking And Firewall Implications

### Single GameServer Reference

The reference `GameServer` still uses:

- UDP `26000`

This repo keeps the existing narrow Terraform firewall rule for that path.

### Fleet Phase

The Fleet uses dynamic host ports from:

- UDP `7000-7010`

That requires a different firewall posture from the single fixed-port phase. Terraform now manages an additional narrow VPC ingress firewall rule for UDP `7000-7010`.

This is the cleanest minimal approach for this phase because it:

- avoids opening the full Agones default `7000-8000` range
- allows two Fleet replicas on one node without port collisions
- keeps the external connection model explicit and inspectable

## What Comes Later

### Later: FleetAutoscaler

Only after the Fleet plus allocation path is proven should the project add `FleetAutoscaler`.

### Later: Allocator Backend Or Frontend

Only after the raw Kubernetes `GameServerAllocation` flow is proven should the project add:

- allocator service integration
- backend or frontend allocation callers
