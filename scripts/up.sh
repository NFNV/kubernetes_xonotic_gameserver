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
manifest_path="${repo_root}/platform/connectivity-checkpoint/manifests/xonotic-server.yaml"
namespace="xonotic-connectivity-checkpoint"
deployment_name="xonotic-connectivity-checkpoint"

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

kubectl apply -f "${manifest_path}"
kubectl rollout status "deployment/${deployment_name}" -n "${namespace}"
kubectl get pods -n "${namespace}"
kubectl get service -n "${namespace}"
