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
gameserver_manifest="${repo_root}/platform/agones/manifests/xonotic-gameserver.yaml"
agones_system_namespace="agones-system"
gameserver_namespace="xonotic-agones"
gameserver_name="xonotic-gameserver"

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
  --set "gameservers.namespaces={${gameserver_namespace}}"

kubectl rollout status deployment/agones-controller -n "${agones_system_namespace}"
kubectl rollout status deployment/agones-extensions -n "${agones_system_namespace}"

kubectl apply -f "${gameserver_manifest}"

for _ in $(seq 1 60); do
  if [[ "$(kubectl get gameserver "${gameserver_name}" -n "${gameserver_namespace}" -o jsonpath='{.status.state}' 2>/dev/null || true)" == "Ready" ]]; then
    break
  fi
  sleep 5
done

if [[ "$(kubectl get gameserver "${gameserver_name}" -n "${gameserver_namespace}" -o jsonpath='{.status.state}' 2>/dev/null || true)" != "Ready" ]]; then
  echo "GameServer ${gameserver_namespace}/${gameserver_name} did not reach Ready" >&2
  exit 1
fi

kubectl get pods -n "${agones_system_namespace}"
kubectl get gameserver -n "${gameserver_namespace}"
kubectl get gameserver "${gameserver_name}" -n "${gameserver_namespace}" -o wide
