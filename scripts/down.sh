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
allocator_frontend_deployment_manifest="${repo_root}/platform/allocator-frontend/manifests/deployment.yaml"
allocator_frontend_service_manifest="${repo_root}/platform/allocator-frontend/manifests/service.yaml"
postgres_pvc_manifest="${repo_root}/platform/postgres/manifests/pvc.yaml"
postgres_deployment_manifest="${repo_root}/platform/postgres/manifests/deployment.yaml"
postgres_service_manifest="${repo_root}/platform/postgres/manifests/service.yaml"
infra_dir="${repo_root}/infra"
rcon_secret_name="xonotic-rcon"
postgres_secret_name="xonotic-postgres"
server_pool_id="${XONOTIC_SERVER_POOL_ID:-south-america-default}"
server_pool_display_name="${XONOTIC_SERVER_POOL_DISPLAY_NAME:-South America - Default}"
server_region="${XONOTIC_SERVER_REGION:-south-america}"
gameserver_namespace="${XONOTIC_AGONES_NAMESPACE:-xonotic-agones}"
fleet_name="${XONOTIC_FLEET_NAME:-xonotic-fleet}"
udp_port_range="${XONOTIC_UDP_PORT_RANGE:-7000-7010}"

kubectl delete -f "${manifest_path}" --ignore-not-found=true || true
kubectl delete -f "${agones_gameserver_manifest}" --ignore-not-found=true || true
kubectl delete -f "${allocator_frontend_service_manifest}" --ignore-not-found=true || true
kubectl delete -f "${allocator_frontend_deployment_manifest}" --ignore-not-found=true || true
kubectl delete -f "${allocator_backend_service_manifest}" --ignore-not-found=true || true
kubectl delete -f "${allocator_backend_deployment_manifest}" --ignore-not-found=true || true
kubectl delete -f "${postgres_deployment_manifest}" --ignore-not-found=true || true
kubectl delete -f "${postgres_service_manifest}" --ignore-not-found=true || true
kubectl delete -f "${postgres_pvc_manifest}" --ignore-not-found=true || true
kubectl delete secret "${postgres_secret_name}" -n xonotic-allocator-backend --ignore-not-found=true || true
kubectl delete secret "${rcon_secret_name}" -n xonotic-allocator-backend --ignore-not-found=true || true
kubectl delete -f "${allocator_backend_rbac_manifest}" --ignore-not-found=true || true
kubectl delete -f "${allocator_backend_namespace_manifest}" --ignore-not-found=true || true
kubectl delete -f "${agones_fleet_autoscaler_manifest}" --ignore-not-found=true || true
kubectl delete -f "${agones_fleet_manifest}" --ignore-not-found=true || true
kubectl delete secret "${rcon_secret_name}" -n "${gameserver_namespace}" --ignore-not-found=true || true

cd "${infra_dir}"

if [[ ! -f terraform.tfvars ]]; then
  cat > terraform.tfvars <<EOF
project_id = "${GCP_PROJECT_ID}"

default_server_pool_id = "${server_pool_id}"

server_pools = {
  "${server_pool_id}" = {
    pool_id          = "${server_pool_id}"
    display_name     = "${server_pool_display_name}"
    region           = "${server_region}"
    gcp_region       = "${GCP_REGION}"
    gcp_zone         = "${GCP_ZONE}"
    cluster_name     = "${GKE_CLUSTER_NAME}"
    agones_namespace = "${gameserver_namespace}"
    fleet_name       = "${fleet_name}"
    udp_port_range   = "${udp_port_range}"
  }
}
EOF
fi

terraform init
terraform destroy -auto-approve
