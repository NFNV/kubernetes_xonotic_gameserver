# GKE Cloud Connectivity Checkpoint

This checkpoint exists to answer one question before Agones or broader platform work continues: can a real Xonotic client join a dedicated server running on GKE over UDP.

## What This Uses

- one public `linux/amd64` container image
- one Kubernetes `Deployment` with `replicas: 1` and `strategy: Recreate`
- one Kubernetes `Service` of type `LoadBalancer` on `26000/udp`
- direct client connect by external IP and port

It intentionally does not add:

- Agones
- ingress
- observability stack
- internal services
- private registry auth
- production hardening

## Why The Image Should Be Public For This Checkpoint

Use a temporary public GHCR image for this proof. That removes image pull secrets and registry auth from the test path, which keeps failures focused on the two things that matter here: server process startup and UDP client reachability.

## Manifests

- `manifests/xonotic-server.yaml.tmpl`: namespace, single-replica deployment, and UDP load balancer service

## Deploy

Prerequisites:

- `gcloud`, `kubectl`, and Docker are installed locally
- you can authenticate to both GCP and GHCR
- you have a real GCP project with billing enabled

## Sequence

The order matters:

1. apply the minimal GCP and GKE infrastructure from `infra/`
2. fetch kubeconfig credentials for the new cluster
3. build and push the server image to GHCR
4. apply the one-server Kubernetes manifests
5. test direct client connectivity

## Step 1: Build And Apply Infrastructure

From the repository root:

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and set at least:

- `project_id`

Optional but likely choices to review before apply:

- `region`
- `zone`
- `cluster_name`

Then run:

```bash
terraform init
terraform plan -out=tfplan
terraform apply tfplan
```

## Step 2: Get Cluster Credentials

After `terraform apply` succeeds, fetch the generated helper command and run it:

```bash
terraform output -raw get_credentials_command
```

Run the printed `gcloud container clusters get-credentials ...` command, then verify cluster access:

```bash
kubectl get nodes
```

Return to the repository root before the next steps:

```bash
cd ..
```

## Step 3: Build And Push The Image

Log in to GHCR.

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
```

Build and push a `linux/amd64` image that GKE can pull.

```bash
export XONOTIC_IMAGE="ghcr.io/<github-user>/xonotic-server:connectivity-proof"
docker buildx build --platform linux/amd64 -t "$XONOTIC_IMAGE" --push ./server
```

## Step 4: Apply The Checkpoint Manifests

Apply the manifest template with your image substituted in.

```bash
sed "s|REPLACE_WITH_PUBLIC_IMAGE|$XONOTIC_IMAGE|g" \
  platform/connectivity-checkpoint/manifests/xonotic-server.yaml.tmpl \
  | kubectl apply -f -
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

## Check Server Logs

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
