# GKE Cloud Connectivity Checkpoint

This checkpoint exists to answer one question before Agones or broader platform work continues: can a real Xonotic client join a dedicated server running on GKE over UDP.

## What This Uses

- one public `linux/amd64` container image
- one Kubernetes `Deployment` with `replicas: 1` and `strategy: Recreate`
- one Kubernetes `Service` of type `LoadBalancer` on `26000/udp` with `externalTrafficPolicy: Local`
- direct client connect by external IP and port

It intentionally does not add:

- Agones
- ingress
- observability stack
- internal services
- private registry auth
- production hardening
- explicit GKE NEG annotations

## Why The Image Should Be Public For This Checkpoint

Use a temporary public GHCR image for this proof. That removes image pull secrets and registry auth from the test path, which keeps failures focused on the two things that matter here: server process startup and UDP client reachability.

## Manifests

- `manifests/xonotic-server.yaml`: namespace, single-replica deployment, and UDP load balancer service pinned to the expected GHCR checkpoint tag

## Deploy

Prerequisites:

- `gcloud` and `kubectl` are installed locally
- the GKE cluster already exists and `kubectl get nodes` works
- you can run GitHub Actions for this repository
- you have a real GCP project with billing enabled

## Sequence

The order matters:

1. publish the server image to GHCR with GitHub Actions
2. verify the GHCR package is public
3. apply the one-server Kubernetes manifest
4. test direct client connectivity

## Step 1: Publish The Image To GHCR

Run the repository workflow at `.github/workflows/publish-server-image.yml` from the GitHub Actions UI.

Expected published image tags:

- `ghcr.io/nfnv/xonotic-server:connectivity-checkpoint`
- `ghcr.io/nfnv/xonotic-server:sha-<12-char-commit>`

The workflow uses the repository `GITHUB_TOKEN` for GHCR login and publishes only the `linux/amd64` image needed for GKE.

## Step 2: Verify Package Visibility

On the first push, verify in GitHub that the `xonotic-server` package is public. Keep the package public for this checkpoint so GKE can pull it without an image pull secret.

## Step 3: Apply The Checkpoint Manifest

The manifest already references the expected checkpoint image tag, so the apply step is direct:

```bash
kubectl apply -f platform/connectivity-checkpoint/manifests/xonotic-server.yaml
```

Wait for the pod to become available.

```bash
kubectl rollout status deployment/xonotic-connectivity-checkpoint -n xonotic-connectivity-checkpoint
```

Watch the load balancer service until it has an external IP.

```bash
kubectl get service xonotic-connectivity-checkpoint -n xonotic-connectivity-checkpoint --watch
```

Capture the external IP once assigned.

```bash
export XONOTIC_SERVER_IP="$(kubectl get service xonotic-connectivity-checkpoint -n xonotic-connectivity-checkpoint -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
echo "$XONOTIC_SERVER_IP"
```

## Step 4: Check Server Logs

Use this before testing from the client so you know the process is actually running:

```bash
kubectl logs deployment/xonotic-connectivity-checkpoint -n xonotic-connectivity-checkpoint --tail=100
```

## Step 5: Test From A Real Client

Use a real Xonotic client on a machine outside the cluster and connect directly to the load balancer IP on UDP port `26000`.

Recommended test flow:

1. Start the Xonotic client.
2. Open the in-game console.
3. Run:

```text
connect <load-balancer-ip>:26000
```

Success criteria:

- the client joins the server
- the server logs show the incoming client connection
- gameplay begins on the server instead of timing out or failing at connect

If the client cannot join, collect:

- `kubectl get pods -n xonotic-connectivity-checkpoint -o wide`
- `kubectl describe service xonotic-connectivity-checkpoint -n xonotic-connectivity-checkpoint`
- `kubectl logs deployment/xonotic-connectivity-checkpoint -n xonotic-connectivity-checkpoint --tail=200`

## Cleanup

```bash
kubectl delete namespace xonotic-connectivity-checkpoint
```
