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

## Phase 2 Reference: Fleet Plus Allocation

The previous Agones step replaced the one-off `GameServer` checkpoint with:

- one small `Fleet`
- one test `GameServerAllocation` flow

That phase stays in the repo as the direct reference for:

1. keeping a warm pool of ready game servers
2. allocating one game server atomically when a match needs one
3. inspecting the returned address and port
4. connecting the client directly to that allocated server

## Phase Now: FleetAutoscaler Buffer

The current Agones phase adds one `FleetAutoscaler` on top of the working Fleet and allocation flow.

The goal is simple:

- keep `3` `Ready` servers on standby
- let Agones grow total Fleet capacity automatically when allocations consume the ready pool
- stop before adding allocator service exposure, frontend work, or more advanced scaling logic

## What A Fleet Is

A `Fleet` is a managed set of warm `GameServer` instances. Instead of creating one named `GameServer` by hand, Agones maintains a desired pool size for you.

For this phase, the Fleet stays deliberately small and acts as the template the autoscaler controls:

- `replicas: 3`

The important shift is that `replicas` is no longer how you reason about steady-state capacity by hand. The `FleetAutoscaler` now owns that behavior.

## What GameServerAllocation Does

A `GameServerAllocation` asks Agones to atomically pick one `Ready` game server that matches the selector and mark it `Allocated`.

That matters because the connection target is no longer a single fixed server:

- the allocation picks one warm server from the Fleet
- Agones returns the server address and the actual external port to use
- the client connects to that specific address and port

## What FleetAutoscaler Changes

A `FleetAutoscaler` sits above the `Fleet` and adjusts its replica count automatically.

For this phase, it uses Agones buffer autoscaling:

- `bufferSize: 3`
- `minReplicas: 3`
- `maxReplicas: 6`

That means:

- Agones tries to keep `3` `Ready` servers available
- if one allocation consumes a `Ready` server, Agones scales the Fleet up so the standby pool returns to `3`
- total Fleet size can grow above `3` while servers are `Allocated`
- Fleet growth is capped at `6` for this small dev cluster

Example:

1. start with `3` `Ready`, `0` `Allocated`
2. allocate one server
3. Fleet briefly has `2` `Ready`, `1` `Allocated`
4. autoscaler increases desired Fleet size
5. once the replacement server becomes `Ready`, the Fleet settles at `3` `Ready`, `1` `Allocated`

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
- `manifests/xonotic-fleet.yaml`: current Fleet template manifest with the autoscaled Xonotic server spec
- `manifests/xonotic-fleetautoscaler.yaml`: buffer autoscaler that keeps `3` `Ready` servers on standby
- `manifests/xonotic-gameserverallocation.yaml`: test allocation manifest using `generateName`

## Startup Map Selection

The startup map is now controlled in the image entrypoint rather than through interactive shell access.

Behavior order:

1. if `XONOTIC_START_MAP` is set, the server starts on that exact map
2. otherwise, if `XONOTIC_RANDOM_START_MAP_ENABLE=1`, the server chooses one random map from `XONOTIC_MAP_POOL`
3. otherwise, the image falls back to the previous behavior, which means no explicit startup map is injected unless the legacy `XONOTIC_MAP` variable is set

The current Fleet manifest enables random startup maps by default:

- `XONOTIC_RANDOM_START_MAP_ENABLE=1`
- `XONOTIC_MAP_POOL="xoylent stormkeep darkzone drain"`

### Set A Fixed Startup Map

In the relevant manifest, set:

```yaml
- name: XONOTIC_START_MAP
  value: stormkeep
```

### Enable Random Startup Maps

In the relevant manifest, set:

```yaml
- name: XONOTIC_RANDOM_START_MAP_ENABLE
  value: "1"
```

### Change The Map Pool

Set a whitespace-separated list:

```yaml
- name: XONOTIC_MAP_POOL
  value: xoylent stormkeep darkzone drain
```

### Make The Change Take Effect

After changing the manifest, recreate the affected Agones servers:

- for the Fleet phase, apply the updated Fleet manifest and either delete the current Fleet `GameServer` Pods or restart the Fleet rollout by changing the template
- for the single-`GameServer` reference, reapply the manifest after deleting the current `GameServer`

For the Fleet phase, the simplest manual refresh is:

```bash
kubectl apply -f platform/agones/manifests/xonotic-fleet.yaml
kubectl delete gameserver -n xonotic-agones -l agones.dev/fleet=xonotic-fleet
kubectl get gameserver -n xonotic-agones -w
```

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

## Apply The Fleet And FleetAutoscaler

Apply the Fleet first:

```bash
kubectl apply -f platform/agones/manifests/xonotic-fleet.yaml
```

Apply the autoscaler:

```bash
kubectl apply -f platform/agones/manifests/xonotic-fleetautoscaler.yaml
```

Watch autoscaler, Fleet, and `GameServer` state:

```bash
kubectl get fleetautoscaler -n xonotic-agones -w
kubectl get fleet -n xonotic-agones -w
kubectl get gameserver -n xonotic-agones -w
```

Inspect them in more detail:

```bash
kubectl describe fleetautoscaler xonotic-fleet-autoscaler -n xonotic-agones
kubectl describe fleet xonotic-fleet -n xonotic-agones
kubectl get gameserver -n xonotic-agones -o wide
```

Success for this step means:

- the autoscaler is attached to `xonotic-fleet`
- the Fleet stabilizes with `3` `Ready` servers
- the backing `GameServer` instances become `Ready`

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

With the autoscaler in place, Agones should then create a replacement server so the standby pool returns to `3` `Ready`.

## Verify Standby Replenishment

Start by confirming the baseline state:

```bash
kubectl get fleet xonotic-fleet -n xonotic-agones -o jsonpath='{.status.readyReplicas}{" ready / "}{.status.allocatedReplicas}{" allocated / "}{.status.replicas}{" total\n"}'
kubectl get gameserver -n xonotic-agones
```

Expected baseline:

- `3 ready / 0 allocated / 3 total`

Create an allocation:

```bash
allocation_name="$(kubectl create -f platform/agones/manifests/xonotic-gameserverallocation.yaml -o name)"
echo "${allocation_name}"
```

Then watch the Fleet recover the standby buffer:

```bash
kubectl get fleet xonotic-fleet -n xonotic-agones -w
kubectl get gameserver -n xonotic-agones -w
```

Expected progression:

1. one `GameServer` becomes `Allocated`
2. `readyReplicas` drops below `3`
3. the `FleetAutoscaler` increases the Fleet replica target
4. a new `GameServer` is created
5. the Fleet returns to `3` `Ready` while the earlier one remains `Allocated`

You can re-check the counts explicitly with:

```bash
kubectl get fleet xonotic-fleet -n xonotic-agones -o jsonpath='{.status.readyReplicas}{" ready / "}{.status.allocatedReplicas}{" allocated / "}{.status.replicas}{" total\n"}'
```

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

### Later: Allocator Backend Or Frontend

The repo already includes the first in-cluster allocator backend, but later work can add:

- allocator service integration
- backend or frontend allocation callers

### Later: More Advanced Scaling

Only after the buffer autoscaling phase is proven should the project consider:

- larger Fleet limits
- more than one node
- custom or webhook-based autoscaling
