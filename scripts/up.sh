#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
env_file="${script_dir}/env.sh"

if [[ -f "${env_file}" ]]; then
  # shellcheck disable=SC1090
  source "${env_file}"
fi

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set}"
: "${GCP_REGION:?GCP_REGION must be set}"
: "${GCP_ZONE:?GCP_ZONE must be set}"
: "${GKE_CLUSTER_NAME:?GKE_CLUSTER_NAME must be set}"

infra_dir="${repo_root}/infra"
agones_namespace_manifest="${repo_root}/platform/agones/manifests/namespace.yaml"
fleet_manifest="${repo_root}/platform/agones/manifests/xonotic-fleet.yaml"
fleet_autoscaler_manifest="${repo_root}/platform/agones/manifests/xonotic-fleetautoscaler.yaml"
allocator_backend_namespace_manifest="${repo_root}/platform/allocator-backend/manifests/namespace.yaml"
allocator_backend_rbac_manifest="${repo_root}/platform/allocator-backend/manifests/rbac.yaml"
allocator_backend_deployment_manifest="${repo_root}/platform/allocator-backend/manifests/deployment.yaml"
allocator_backend_service_manifest="${repo_root}/platform/allocator-backend/manifests/service.yaml"
allocator_frontend_deployment_manifest="${repo_root}/platform/allocator-frontend/manifests/deployment.yaml"
allocator_frontend_service_manifest="${repo_root}/platform/allocator-frontend/manifests/service.yaml"
agones_system_namespace="agones-system"
gameserver_namespace="xonotic-agones"
fleet_name="xonotic-fleet"
required_ready_replicas="3"
allocator_backend_namespace="xonotic-allocator-backend"
allocator_backend_deployment_name="xonotic-allocator-backend"
allocator_frontend_deployment_name="xonotic-allocator-frontend"

cd "${infra_dir}"

if [[ ! -f terraform.tfvars ]]; then
  cat > terraform.tfvars <<EOF
project_id   = "${GCP_PROJECT_ID}"
region       = "${GCP_REGION}"
zone         = "${GCP_ZONE}"
cluster_name = "${GKE_CLUSTER_NAME}"
EOF
fi

terraform init
terraform apply -auto-approve

credentials_command="$(terraform output -raw get_credentials_command)"
bash -lc "${credentials_command}"

kubectl apply -f "${agones_namespace_manifest}"

helm repo add agones https://agones.dev/chart/stable --force-update
helm repo update
helm upgrade --install agones agones/agones \
  --namespace "${agones_system_namespace}" \
  --create-namespace \
  --set agones.ping.install=false \
  --set gameservers.minPort=7000 \
  --set gameservers.maxPort=7010 \
  --set "gameservers.namespaces={${gameserver_namespace}}"

kubectl rollout status deployment/agones-controller -n "${agones_system_namespace}"
kubectl rollout status deployment/agones-extensions -n "${agones_system_namespace}"

kubectl apply -f "${fleet_manifest}"
kubectl delete gameserver -n "${gameserver_namespace}" -l "agones.dev/fleet=${fleet_name}" --ignore-not-found=true || true
kubectl apply -f "${fleet_autoscaler_manifest}"

for _ in $(seq 1 60); do
  ready_replicas="$(kubectl get fleet "${fleet_name}" -n "${gameserver_namespace}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  if [[ -n "${ready_replicas}" ]] && (( ready_replicas >= required_ready_replicas )); then
    break
  fi
  sleep 5
done

ready_replicas="$(kubectl get fleet "${fleet_name}" -n "${gameserver_namespace}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
if [[ -z "${ready_replicas}" ]] || (( ready_replicas < required_ready_replicas )); then
  echo "Fleet ${gameserver_namespace}/${fleet_name} did not reach ${required_ready_replicas} Ready replicas" >&2
  exit 1
fi

kubectl apply -f "${allocator_backend_namespace_manifest}"
kubectl apply -f "${allocator_backend_rbac_manifest}"
kubectl apply -f "${allocator_backend_deployment_manifest}"
kubectl apply -f "${allocator_backend_service_manifest}"
kubectl rollout status "deployment/${allocator_backend_deployment_name}" -n "${allocator_backend_namespace}"
kubectl apply -f "${allocator_frontend_deployment_manifest}"
kubectl apply -f "${allocator_frontend_service_manifest}"
kubectl rollout status "deployment/${allocator_frontend_deployment_name}" -n "${allocator_backend_namespace}"

kubectl get pods -n "${agones_system_namespace}"
kubectl get fleetautoscaler -n "${gameserver_namespace}"
kubectl get fleet -n "${gameserver_namespace}"
kubectl get gameserver -n "${gameserver_namespace}" -o custom-columns=NAME:.metadata.name,STATE:.status.state,ADDRESS:.status.address,PORT:.status.ports[0].port,NODE:.status.nodeName
kubectl get pods -n "${allocator_backend_namespace}"
kubectl get service -n "${allocator_backend_namespace}"
