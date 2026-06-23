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

allow_missing_secondary=0
if [[ "${1:-}" == "--allow-missing-secondary" ]]; then
  allow_missing_secondary=1
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--allow-missing-secondary]" >&2
  exit 1
fi

south_america_context="${XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT:-gke_${GCP_PROJECT_ID}_southamerica-west1-a_xonotic-mvp}"
europe_context="${XONOTIC_EUROPE_KUBE_CONTEXT:-gke_${GCP_PROJECT_ID}_europe-west1-b_xonotic-eu}"
north_america_context="${XONOTIC_NORTH_AMERICA_KUBE_CONTEXT:-gke_${GCP_PROJECT_ID}_us-central1-a_xonotic-na}"
output_path="${XONOTIC_MULTICLUSTER_KUBECONFIG:-${script_dir}/.generated/xonotic-multicluster.kubeconfig}"
rbac_manifest="${repo_root}/platform/agones/manifests/regional-allocator-rbac.yaml"
namespace="${XONOTIC_AGONES_NAMESPACE:-xonotic-agones}"
token_secret="xonotic-regional-allocator-token"

mkdir -p "$(dirname "${output_path}")"
rm -f "${output_path}"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

add_context() {
  local source_context="$1"
  local required="$2"
  local ca_data
  local server
  local token
  local ca_file="${tmp_dir}/${source_context//[^a-zA-Z0-9_.-]/_}.crt"

  if ! kubectl config get-contexts "${source_context}" -o name | grep -Fxq "${source_context}"; then
    if [[ "${required}" != "1" ]]; then
      echo "Skipping unavailable secondary kubeconfig context: ${source_context}" >&2
      return 0
    fi
    echo "Missing kubeconfig context: ${source_context}" >&2
    echo "Run the documented gcloud container clusters get-credentials command for this region first." >&2
    exit 1
  fi

  if ! kubectl --context "${source_context}" get namespace "${namespace}" --request-timeout=10s >/dev/null 2>&1; then
    if [[ "${required}" != "1" ]]; then
      echo "Skipping unreachable secondary kubeconfig context: ${source_context}" >&2
      return 0
    fi
    echo "Primary kubeconfig context is unreachable: ${source_context}" >&2
    echo "Refresh it with the documented gcloud container clusters get-credentials command." >&2
    exit 1
  fi

  if ! kubectl --context "${source_context}" apply -f "${rbac_manifest}"; then
    if [[ "${required}" != "1" ]]; then
      echo "Skipping secondary context after RBAC apply failed: ${source_context}" >&2
      return 0
    fi
    echo "Failed to apply regional allocator RBAC in primary context: ${source_context}" >&2
    exit 1
  fi

  for _ in $(seq 1 30); do
    token="$(kubectl --context "${source_context}" get secret "${token_secret}" -n "${namespace}" -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)"
    if [[ -n "${token}" ]]; then
      break
    fi
    sleep 1
  done

  if [[ -z "${token:-}" ]]; then
    if [[ "${required}" != "1" ]]; then
      echo "Skipping secondary context because its allocator token is unavailable: ${source_context}" >&2
      return 0
    fi
    echo "Token Secret ${namespace}/${token_secret} was not populated in context ${source_context}" >&2
    exit 1
  fi

  server="$(kubectl --context "${source_context}" config view --minify --flatten --raw -o jsonpath='{.clusters[0].cluster.server}')"
  ca_data="$(kubectl --context "${source_context}" config view --minify --flatten --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
  if [[ -z "${server}" || -z "${ca_data}" ]]; then
    if [[ "${required}" != "1" ]]; then
      echo "Skipping secondary context because its API server or CA is unavailable: ${source_context}" >&2
      return 0
    fi
    echo "Context ${source_context} is missing an API server or embedded cluster CA" >&2
    exit 1
  fi

  printf '%s' "${ca_data}" | base64 -d > "${ca_file}"
  kubectl config --kubeconfig="${output_path}" set-cluster "${source_context}" \
    --server="${server}" \
    --certificate-authority="${ca_file}" \
    --embed-certs=true >/dev/null
  kubectl config --kubeconfig="${output_path}" set-credentials "${source_context}" --token="${token}" >/dev/null
  kubectl config --kubeconfig="${output_path}" set-context "${source_context}" \
    --cluster="${source_context}" \
    --user="${source_context}" \
    --namespace="${namespace}" >/dev/null
}

add_context "${south_america_context}" 1
add_context "${europe_context}" "$((1 - allow_missing_secondary))"
add_context "${north_america_context}" "$((1 - allow_missing_secondary))"

kubectl config --kubeconfig="${output_path}" use-context "${south_america_context}" >/dev/null
chmod 600 "${output_path}"

cat <<EOF
Created least-privilege regional allocator kubeconfig:
  ${output_path}

Add or keep these values in scripts/env.sh:
export XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT="${south_america_context}"
export XONOTIC_EUROPE_KUBE_CONTEXT="${europe_context}"
export XONOTIC_NORTH_AMERICA_KUBE_CONTEXT="${north_america_context}"
export XONOTIC_MULTICLUSTER_KUBECONFIG="${output_path}"
EOF
