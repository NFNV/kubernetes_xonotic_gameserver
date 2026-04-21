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

manifest_path="${repo_root}/platform/connectivity-checkpoint/manifests/xonotic-server.yaml"
agones_gameserver_manifest="${repo_root}/platform/agones/manifests/xonotic-gameserver.yaml"
agones_fleet_manifest="${repo_root}/platform/agones/manifests/xonotic-fleet.yaml"
agones_fleet_autoscaler_manifest="${repo_root}/platform/agones/manifests/xonotic-fleetautoscaler.yaml"
allocator_backend_namespace_manifest="${repo_root}/platform/allocator-backend/manifests/namespace.yaml"
allocator_backend_rbac_manifest="${repo_root}/platform/allocator-backend/manifests/rbac.yaml"
allocator_backend_deployment_manifest="${repo_root}/platform/allocator-backend/manifests/deployment.yaml"
allocator_backend_service_manifest="${repo_root}/platform/allocator-backend/manifests/service.yaml"
infra_dir="${repo_root}/infra"

kubectl delete -f "${manifest_path}" --ignore-not-found=true || true
kubectl delete -f "${agones_gameserver_manifest}" --ignore-not-found=true || true
kubectl delete -f "${allocator_backend_service_manifest}" --ignore-not-found=true || true
kubectl delete -f "${allocator_backend_deployment_manifest}" --ignore-not-found=true || true
kubectl delete -f "${allocator_backend_rbac_manifest}" --ignore-not-found=true || true
kubectl delete -f "${allocator_backend_namespace_manifest}" --ignore-not-found=true || true
kubectl delete -f "${agones_fleet_autoscaler_manifest}" --ignore-not-found=true || true
kubectl delete -f "${agones_fleet_manifest}" --ignore-not-found=true || true

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
terraform destroy -auto-approve
