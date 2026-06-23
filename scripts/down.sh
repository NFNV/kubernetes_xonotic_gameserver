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
primary_region="south-america"
primary_tfvars_file="regions/${primary_region}.tfvars"
rcon_secret_name="xonotic-rcon"
postgres_secret_name="xonotic-postgres"
admin_auth_secret_name="xonotic-admin-auth"
multicluster_kubeconfig_secret_name="xonotic-multicluster-kubeconfig"
gameserver_namespace="${XONOTIC_AGONES_NAMESPACE:-xonotic-agones}"

terraform -chdir="${infra_dir}" init
if ! terraform -chdir="${infra_dir}" workspace select "${primary_region}"; then
  echo "Terraform workspace '${primary_region}' does not exist. Nothing to destroy."
  exit 0
fi

credentials_command="$(terraform -chdir="${infra_dir}" output -raw get_credentials_command 2>/dev/null || true)"
primary_context_ready=0
if [[ -n "${credentials_command}" ]]; then
  if bash -lc "${credentials_command}"; then
    primary_context_ready=1
  else
    echo "Warning: could not refresh South America kubeconfig; skipping Kubernetes cleanup and continuing with Terraform destroy." >&2
  fi
fi

if [[ "${primary_context_ready}" == "1" ]]; then
  kubectl delete -f "${manifest_path}" --ignore-not-found=true || true
  kubectl delete -f "${agones_gameserver_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${allocator_frontend_service_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${allocator_frontend_deployment_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${allocator_backend_service_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${allocator_backend_deployment_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${postgres_deployment_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${postgres_service_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${postgres_pvc_manifest}" --ignore-not-found=true || true
  kubectl delete secret "${admin_auth_secret_name}" -n xonotic-allocator-backend --ignore-not-found=true || true
  kubectl delete secret "${multicluster_kubeconfig_secret_name}" -n xonotic-allocator-backend --ignore-not-found=true || true
  kubectl delete secret "${postgres_secret_name}" -n xonotic-allocator-backend --ignore-not-found=true || true
  kubectl delete secret "${rcon_secret_name}" -n xonotic-allocator-backend --ignore-not-found=true || true
  kubectl delete -f "${allocator_backend_rbac_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${allocator_backend_namespace_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${agones_fleet_autoscaler_manifest}" --ignore-not-found=true || true
  kubectl delete -f "${agones_fleet_manifest}" --ignore-not-found=true || true
  kubectl delete secret "${rcon_secret_name}" -n "${gameserver_namespace}" --ignore-not-found=true || true
fi

terraform -chdir="${infra_dir}" destroy -auto-approve \
  -var-file="${primary_tfvars_file}" \
  -var="project_id=${GCP_PROJECT_ID}" \
  -var="region=${GCP_REGION}" \
  -var="zone=${GCP_ZONE}" \
  -var="cluster_name=${GKE_CLUSTER_NAME}"
