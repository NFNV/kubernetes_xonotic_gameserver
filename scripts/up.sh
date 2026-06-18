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
: "${XONOTIC_RCON_PASSWORD:?XONOTIC_RCON_PASSWORD must be set}"
: "${XONOTIC_POSTGRES_DB:?XONOTIC_POSTGRES_DB must be set}"
: "${XONOTIC_POSTGRES_USER:?XONOTIC_POSTGRES_USER must be set}"
: "${XONOTIC_POSTGRES_PASSWORD:?XONOTIC_POSTGRES_PASSWORD must be set}"

fail_admin_auth_config() {
  cat >&2 <<'EOF'
Admin auth is required, but ADMIN_PASSWORD_HASH or ADMIN_SESSION_SECRET is missing or invalid.

Generate values with:
  scripts/generate-admin-auth.sh --username admin --password admin

Then paste the printed export lines into gitignored scripts/env.sh, run:
  source scripts/env.sh
  ./scripts/up.sh

Important: keep the generated single quotes around ADMIN_PASSWORD_HASH. Werkzeug password hashes contain "$" separators, and double-quoted or unquoted values can be corrupted by shell expansion.
EOF
  exit 1
}

if [[ -z "${ADMIN_PASSWORD_HASH:-}" || -z "${ADMIN_SESSION_SECRET:-}" ]]; then
  fail_admin_auth_config
fi

pbkdf2_hash_regex='^pbkdf2:sha256:[0-9]+\$[^$]+\$[0-9a-f]+$'
scrypt_hash_regex='^scrypt:[0-9]+:[0-9]+:[0-9]+\$[^$]+\$[0-9a-f]+$'
if [[ ! "${ADMIN_PASSWORD_HASH}" =~ ${pbkdf2_hash_regex} && ! "${ADMIN_PASSWORD_HASH}" =~ ${scrypt_hash_regex} ]]; then
  fail_admin_auth_config
fi

if (( ${#ADMIN_SESSION_SECRET} < 32 )); then
  fail_admin_auth_config
fi

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
postgres_pvc_manifest="${repo_root}/platform/postgres/manifests/pvc.yaml"
postgres_deployment_manifest="${repo_root}/platform/postgres/manifests/deployment.yaml"
postgres_service_manifest="${repo_root}/platform/postgres/manifests/service.yaml"
agones_system_namespace="agones-system"
server_pool_id="${XONOTIC_SERVER_POOL_ID:-south-america-default}"
server_pool_display_name="${XONOTIC_SERVER_POOL_DISPLAY_NAME:-South America - Default}"
server_region="${XONOTIC_SERVER_REGION:-south-america}"
gameserver_namespace="${XONOTIC_AGONES_NAMESPACE:-xonotic-agones}"
fleet_name="${XONOTIC_FLEET_NAME:-xonotic-fleet}"
udp_port_range="${XONOTIC_UDP_PORT_RANGE:-7000-7010}"
udp_min_port="${udp_port_range%-*}"
udp_max_port="${udp_port_range#*-}"
# Default to 1 Ready server for the small single-node dev cluster; override for higher-capacity testing.
required_ready_replicas="${XONOTIC_REQUIRED_READY_REPLICAS:-1}"
allocator_backend_namespace="xonotic-allocator-backend"
allocator_backend_deployment_name="xonotic-allocator-backend"
allocator_frontend_deployment_name="xonotic-allocator-frontend"
postgres_deployment_name="xonotic-postgres"
rcon_secret_name="xonotic-rcon"
postgres_secret_name="xonotic-postgres"
admin_auth_secret_name="xonotic-admin-auth"
admin_username="${ADMIN_USERNAME:-admin}"
rcon_password_b64="$(printf '%s' "${XONOTIC_RCON_PASSWORD}" | base64 | tr -d '\n')"
postgres_db_b64="$(printf '%s' "${XONOTIC_POSTGRES_DB}" | base64 | tr -d '\n')"
postgres_user_b64="$(printf '%s' "${XONOTIC_POSTGRES_USER}" | base64 | tr -d '\n')"
postgres_password_b64="$(printf '%s' "${XONOTIC_POSTGRES_PASSWORD}" | base64 | tr -d '\n')"
admin_username_b64="$(printf '%s' "${admin_username}" | base64 | tr -d '\n')"
admin_password_hash_b64="$(printf '%s' "${ADMIN_PASSWORD_HASH}" | base64 | tr -d '\n')"
admin_session_secret_b64="$(printf '%s' "${ADMIN_SESSION_SECRET}" | base64 | tr -d '\n')"

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
terraform apply -auto-approve

credentials_command="$(terraform output -raw get_credentials_command)"
bash -lc "${credentials_command}"

kubectl apply -f "${agones_namespace_manifest}"
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
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${postgres_secret_name}
  namespace: ${allocator_backend_namespace}
type: Opaque
data:
  POSTGRES_DB: ${postgres_db_b64}
  POSTGRES_USER: ${postgres_user_b64}
  POSTGRES_PASSWORD: ${postgres_password_b64}
EOF
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${admin_auth_secret_name}
  namespace: ${allocator_backend_namespace}
type: Opaque
data:
  ADMIN_USERNAME: ${admin_username_b64}
  ADMIN_PASSWORD_HASH: ${admin_password_hash_b64}
  ADMIN_SESSION_SECRET: ${admin_session_secret_b64}
EOF
kubectl apply -f "${postgres_pvc_manifest}"
kubectl apply -f "${postgres_service_manifest}"
kubectl apply -f "${postgres_deployment_manifest}"
kubectl rollout status "deployment/${postgres_deployment_name}" -n "${allocator_backend_namespace}"
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${rcon_secret_name}
  namespace: ${allocator_backend_namespace}
type: Opaque
data:
  XONOTIC_RCON_PASSWORD: ${rcon_password_b64}
EOF
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
kubectl get deployment "${postgres_deployment_name}" -n "${allocator_backend_namespace}"
kubectl get pvc -n "${allocator_backend_namespace}"
kubectl get pods -n "${allocator_backend_namespace}"
kubectl get service -n "${allocator_backend_namespace}"
