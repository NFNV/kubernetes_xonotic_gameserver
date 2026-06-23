#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/up-region.sh <south-america|europe|north-america>
  ./scripts/down-region.sh <south-america|europe|north-america>
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

region="$1"
case "${region}" in
  south-america | europe | north-america) ;;
  *)
    usage
    exit 1
    ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
env_file="${script_dir}/env.sh"

if [[ -f "${env_file}" ]]; then
  # shellcheck disable=SC1090
  source "${env_file}"
fi

infra_dir="${repo_root}/infra"
tfvars_file="${infra_dir}/regions/${region}.tfvars"
plan_file="${TMPDIR:-/tmp}/xonotic-${region}.tfplan"
agones_namespace_manifest="${repo_root}/platform/agones/manifests/namespace.yaml"
regional_allocator_rbac_manifest="${repo_root}/platform/agones/manifests/regional-allocator-rbac.yaml"
fleet_manifest="${repo_root}/platform/agones/manifests/xonotic-fleet.yaml"
fleet_autoscaler_manifest="${repo_root}/platform/agones/manifests/xonotic-fleetautoscaler.yaml"
agones_system_namespace="agones-system"
gameserver_namespace="${XONOTIC_AGONES_NAMESPACE:-xonotic-agones}"
fleet_name="${XONOTIC_FLEET_NAME:-xonotic-fleet}"
udp_port_range="${XONOTIC_UDP_PORT_RANGE:-7000-7010}"
udp_min_port="${udp_port_range%-*}"
udp_max_port="${udp_port_range#*-}"
required_ready_replicas="${XONOTIC_REQUIRED_READY_REPLICAS:-1}"
rcon_secret_name="xonotic-rcon"

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set}"
: "${XONOTIC_RCON_PASSWORD:?XONOTIC_RCON_PASSWORD must be set}"

if [[ ! -f "${tfvars_file}" ]]; then
  echo "Missing Terraform variables file: ${tfvars_file}" >&2
  exit 1
fi

rcon_password_b64="$(printf '%s' "${XONOTIC_RCON_PASSWORD}" | base64 | tr -d '\n')"

restore_workspace() {
  if [[ -n "${previous_workspace:-}" ]]; then
    terraform -chdir="${infra_dir}" workspace select "${previous_workspace}" >/dev/null 2>&1 || true
  fi
}

cat <<EOF
Selected region: ${region}
Terraform tfvars: ${tfvars_file}
Terraform project: ${GCP_PROJECT_ID}

This provisions the regional GKE/firewall Terraform layer and then deploys the
regional Agones/Xonotic game-server plane.
It does not deploy a duplicate allocator backend, frontend, PostgreSQL, or observability stack.
Use ./scripts/up.sh for the current full South America dev control-plane workflow.
EOF

if [[ "${region}" != "south-america" ]]; then
  cat <<EOF

Cost warning: ${region} is an additional opt-in region. Applying this plan can create another
GKE cluster and node pool in your GCP project.
EOF
fi

terraform -chdir="${infra_dir}" init
previous_workspace="$(terraform -chdir="${infra_dir}" workspace show 2>/dev/null || printf 'default')"
trap restore_workspace EXIT

if ! terraform -chdir="${infra_dir}" workspace select "${region}"; then
  terraform -chdir="${infra_dir}" workspace new "${region}"
fi

active_workspace="$(terraform -chdir="${infra_dir}" workspace show)"
echo "Terraform workspace: ${active_workspace}"

terraform -chdir="${infra_dir}" plan \
  -var-file="regions/${region}.tfvars" \
  -var="project_id=${GCP_PROJECT_ID}" \
  -out="${plan_file}"

if [[ "${XONOTIC_AUTO_APPROVE:-0}" != "1" ]]; then
  echo
  read -r -p "Type '${region}' to apply this regional infrastructure: " confirmation
  if [[ "${confirmation}" != "${region}" ]]; then
    echo "Aborted. No infrastructure was changed."
    exit 1
  fi
fi

terraform -chdir="${infra_dir}" apply "${plan_file}"

credentials_command="$(terraform -chdir="${infra_dir}" output -raw get_credentials_command)"
bash -lc "${credentials_command}"

echo
echo "Deploying regional game-server plane into ${region}..."

kubectl apply -f "${agones_namespace_manifest}"
kubectl apply -f "${regional_allocator_rbac_manifest}"
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${rcon_secret_name}
  namespace: ${gameserver_namespace}
type: Opaque
data:
  XONOTIC_RCON_PASSWORD: ${rcon_password_b64}
EOF

helm repo add agones https://agones.dev/chart/stable --force-update
helm repo update
helm upgrade --install agones agones/agones \
  --namespace "${agones_system_namespace}" \
  --create-namespace \
  --set agones.ping.install=false \
  --set "gameservers.minPort=${udp_min_port}" \
  --set "gameservers.maxPort=${udp_max_port}" \
  --set "gameservers.namespaces={${gameserver_namespace}}"

kubectl rollout status deployment/agones-controller -n "${agones_system_namespace}"
kubectl rollout status deployment/agones-extensions -n "${agones_system_namespace}"

kubectl apply -f "${fleet_manifest}"
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

echo
terraform -chdir="${infra_dir}" output default_server_pool_id
terraform -chdir="${infra_dir}" output default_server_pool
terraform -chdir="${infra_dir}" output get_credentials_command

kubectl get pods -n "${agones_system_namespace}"
kubectl get fleetautoscaler -n "${gameserver_namespace}"
kubectl get fleet -n "${gameserver_namespace}"
kubectl get gameserver -n "${gameserver_namespace}" -o wide
