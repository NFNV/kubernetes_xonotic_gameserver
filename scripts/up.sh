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
primary_region="south-america"
primary_tfvars_file="regions/${primary_region}.tfvars"
agones_namespace_manifest="${repo_root}/platform/agones/manifests/namespace.yaml"
regional_allocator_rbac_manifest="${repo_root}/platform/agones/manifests/regional-allocator-rbac.yaml"
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
multicluster_kubeconfig_secret_name="xonotic-multicluster-kubeconfig"
multicluster_kubeconfig_path="${XONOTIC_MULTICLUSTER_KUBECONFIG:-${script_dir}/.generated/xonotic-multicluster.kubeconfig}"
south_america_kube_context="${XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT:-gke_${GCP_PROJECT_ID}_southamerica-west1-a_xonotic-mvp}"
europe_kube_context="${XONOTIC_EUROPE_KUBE_CONTEXT:-gke_${GCP_PROJECT_ID}_europe-west1-b_xonotic-eu}"
north_america_kube_context="${XONOTIC_NORTH_AMERICA_KUBE_CONTEXT:-gke_${GCP_PROJECT_ID}_us-central1-a_xonotic-na}"
admin_username="${ADMIN_USERNAME:-admin}"
rcon_password_b64="$(printf '%s' "${XONOTIC_RCON_PASSWORD}" | base64 | tr -d '\n')"
postgres_db_b64="$(printf '%s' "${XONOTIC_POSTGRES_DB}" | base64 | tr -d '\n')"
postgres_user_b64="$(printf '%s' "${XONOTIC_POSTGRES_USER}" | base64 | tr -d '\n')"
postgres_password_b64="$(printf '%s' "${XONOTIC_POSTGRES_PASSWORD}" | base64 | tr -d '\n')"
admin_username_b64="$(printf '%s' "${admin_username}" | base64 | tr -d '\n')"
admin_password_hash_b64="$(printf '%s' "${ADMIN_PASSWORD_HASH}" | base64 | tr -d '\n')"
admin_session_secret_b64="$(printf '%s' "${ADMIN_SESSION_SECRET}" | base64 | tr -d '\n')"

cat <<EOF
Bringing up the primary Xonotic environment:
  South America GKE/Agones game-server plane
  PostgreSQL
  allocator backend
  allocator frontend

Terraform workspace: ${primary_region}
Terraform variables: ${primary_tfvars_file}
Observability remains an optional separate deployment.
EOF

terraform -chdir="${infra_dir}" init
if ! terraform -chdir="${infra_dir}" workspace select "${primary_region}"; then
  terraform -chdir="${infra_dir}" workspace new "${primary_region}"
fi

terraform_vars=(
  "-var-file=${primary_tfvars_file}"
  "-var=project_id=${GCP_PROJECT_ID}"
  "-var=region=${GCP_REGION}"
  "-var=zone=${GCP_ZONE}"
  "-var=cluster_name=${GKE_CLUSTER_NAME}"
)

terraform_state_has() {
  terraform -chdir="${infra_dir}" state list 2>/dev/null | grep -Fxq "$1"
}

import_existing_primary_resources() {
  local node_pool_name="${GKE_CLUSTER_NAME}-pool"
  local fixed_udp_firewall="${GKE_CLUSTER_NAME}-agones-udp-26000"
  local dynamic_udp_firewall="${GKE_CLUSTER_NAME}-agones-udp-${udp_min_port}-${udp_max_port}"

  if ! terraform_state_has "google_container_cluster.primary" \
    && gcloud container clusters describe "${GKE_CLUSTER_NAME}" \
      --zone "${GCP_ZONE}" \
      --project "${GCP_PROJECT_ID}" >/dev/null 2>&1; then
    echo "Importing existing South America GKE cluster into Terraform workspace ${primary_region}..."
    terraform -chdir="${infra_dir}" import "${terraform_vars[@]}" \
      google_container_cluster.primary \
      "projects/${GCP_PROJECT_ID}/locations/${GCP_ZONE}/clusters/${GKE_CLUSTER_NAME}"
  fi

  if ! terraform_state_has "google_container_node_pool.primary" \
    && gcloud container node-pools describe "${node_pool_name}" \
      --cluster "${GKE_CLUSTER_NAME}" \
      --zone "${GCP_ZONE}" \
      --project "${GCP_PROJECT_ID}" >/dev/null 2>&1; then
    echo "Importing existing South America GKE node pool into Terraform workspace ${primary_region}..."
    terraform -chdir="${infra_dir}" import "${terraform_vars[@]}" \
      google_container_node_pool.primary \
      "projects/${GCP_PROJECT_ID}/locations/${GCP_ZONE}/clusters/${GKE_CLUSTER_NAME}/nodePools/${node_pool_name}"
  fi

  if ! terraform_state_has "google_compute_firewall.xonotic_agones_gameserver_udp" \
    && gcloud compute firewall-rules describe "${fixed_udp_firewall}" \
      --project "${GCP_PROJECT_ID}" >/dev/null 2>&1; then
    echo "Importing existing fixed UDP firewall rule into Terraform workspace ${primary_region}..."
    terraform -chdir="${infra_dir}" import "${terraform_vars[@]}" \
      google_compute_firewall.xonotic_agones_gameserver_udp \
      "projects/${GCP_PROJECT_ID}/global/firewalls/${fixed_udp_firewall}"
  fi

  if ! terraform_state_has "google_compute_firewall.xonotic_agones_fleet_udp_dynamic" \
    && gcloud compute firewall-rules describe "${dynamic_udp_firewall}" \
      --project "${GCP_PROJECT_ID}" >/dev/null 2>&1; then
    echo "Importing existing dynamic UDP firewall rule into Terraform workspace ${primary_region}..."
    terraform -chdir="${infra_dir}" import "${terraform_vars[@]}" \
      google_compute_firewall.xonotic_agones_fleet_udp_dynamic \
      "projects/${GCP_PROJECT_ID}/global/firewalls/${dynamic_udp_firewall}"
  fi
}

import_existing_primary_resources

terraform -chdir="${infra_dir}" apply -auto-approve \
  "${terraform_vars[@]}"

credentials_command="$(terraform -chdir="${infra_dir}" output -raw get_credentials_command)"
bash -lc "${credentials_command}"

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

bash "${script_dir}/build-multicluster-kubeconfig.sh" --allow-missing-secondary
multicluster_kubeconfig_b64="$(base64 < "${multicluster_kubeconfig_path}" | tr -d '\n')"
south_america_kube_context_b64="$(printf '%s' "${south_america_kube_context}" | base64 | tr -d '\n')"
europe_kube_context_b64="$(printf '%s' "${europe_kube_context}" | base64 | tr -d '\n')"
north_america_kube_context_b64="$(printf '%s' "${north_america_kube_context}" | base64 | tr -d '\n')"

kubectl apply -f "${allocator_backend_namespace_manifest}"
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${multicluster_kubeconfig_secret_name}
  namespace: ${allocator_backend_namespace}
type: Opaque
data:
  config: ${multicluster_kubeconfig_b64}
  XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT: ${south_america_kube_context_b64}
  XONOTIC_EUROPE_KUBE_CONTEXT: ${europe_kube_context_b64}
  XONOTIC_NORTH_AMERICA_KUBE_CONTEXT: ${north_america_kube_context_b64}
EOF
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
kubectl wait --for=condition=Ready pod \
  -l app="${postgres_deployment_name}" \
  -n "${allocator_backend_namespace}" \
  --timeout=300s
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
kubectl wait --for=condition=Ready pod \
  -l app="${allocator_backend_deployment_name}" \
  -n "${allocator_backend_namespace}" \
  --timeout=300s
kubectl apply -f "${allocator_frontend_deployment_manifest}"
kubectl apply -f "${allocator_frontend_service_manifest}"
kubectl rollout status "deployment/${allocator_frontend_deployment_name}" -n "${allocator_backend_namespace}"
kubectl wait --for=condition=Ready pod \
  -l app="${allocator_frontend_deployment_name}" \
  -n "${allocator_backend_namespace}" \
  --timeout=300s

kubectl get pods -n "${agones_system_namespace}"
kubectl get fleetautoscaler -n "${gameserver_namespace}"
kubectl get fleet -n "${gameserver_namespace}"
kubectl get gameserver -n "${gameserver_namespace}" -o custom-columns=NAME:.metadata.name,STATE:.status.state,ADDRESS:.status.address,PORT:.status.ports[0].port,NODE:.status.nodeName
kubectl get deployment "${postgres_deployment_name}" -n "${allocator_backend_namespace}"
kubectl get pvc -n "${allocator_backend_namespace}"
kubectl get pods -n "${allocator_backend_namespace}"
kubectl get service -n "${allocator_backend_namespace}"

cat <<EOF

Primary South America environment is ready.

Frontend:
  kubectl port-forward -n ${allocator_backend_namespace} service/xonotic-allocator-frontend 18080:8080
  http://127.0.0.1:18080

Backend:
  kubectl port-forward -n ${allocator_backend_namespace} service/xonotic-allocator-backend 18082:8080
  http://127.0.0.1:18082

Optional observability deployment:
  kubectl apply -k platform/observability
  kubectl rollout status deployment/xonotic-prometheus -n xonotic-observability
  kubectl rollout status deployment/xonotic-grafana -n xonotic-observability
EOF
