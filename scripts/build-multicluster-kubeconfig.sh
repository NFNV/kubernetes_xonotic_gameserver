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

if [[ $# -gt 0 ]]; then
  echo "Usage: $0" >&2
  exit 1
fi

expected_south_america_context="gke_${GCP_PROJECT_ID}_southamerica-west1-a_xonotic-mvp"
expected_europe_context="gke_${GCP_PROJECT_ID}_europe-west1-b_xonotic-eu"
expected_north_america_context="gke_${GCP_PROJECT_ID}_us-central1-a_xonotic-na"
south_america_context="${XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT:-${expected_south_america_context}}"
europe_context="${XONOTIC_EUROPE_KUBE_CONTEXT:-${expected_europe_context}}"
north_america_context="${XONOTIC_NORTH_AMERICA_KUBE_CONTEXT:-${expected_north_america_context}}"
canonical_output_path="${script_dir}/.generated/xonotic-multicluster.kubeconfig"
if [[ "${XONOTIC_MULTICLUSTER_KUBECONFIG+x}" == "x" && -z "${XONOTIC_MULTICLUSTER_KUBECONFIG}" ]]; then
  echo "XONOTIC_MULTICLUSTER_KUBECONFIG is set but empty." >&2
  echo "Unset it or set it to scripts/.generated/xonotic-multicluster.kubeconfig." >&2
  exit 1
fi
configured_output_path="${XONOTIC_MULTICLUSTER_KUBECONFIG:-${canonical_output_path}}"
if [[ "${configured_output_path}" = /* ]]; then
  output_path="${configured_output_path}"
else
  output_path="${repo_root}/${configured_output_path#./}"
fi
rbac_manifest="${repo_root}/platform/agones/manifests/regional-allocator-rbac.yaml"
namespace="${XONOTIC_AGONES_NAMESPACE:-xonotic-agones}"
token_secret="xonotic-regional-allocator-token"
required_contexts=(
  "${south_america_context}"
)
optional_contexts=(
  "${europe_context}"
  "${north_america_context}"
)
included_contexts=()
skipped_contexts=()

if [[ "${south_america_context}" != "${expected_south_america_context}" \
  || "${europe_context}" != "${expected_europe_context}" \
  || "${north_america_context}" != "${expected_north_america_context}" ]]; then
  echo "Configured regional context names do not match the expected GKE contexts for project ${GCP_PROJECT_ID}." >&2
  echo "Expected:" >&2
  printf '  %s\n' "${expected_south_america_context}" "${expected_europe_context}" "${expected_north_america_context}" >&2
  exit 1
fi

mkdir -p "$(dirname "${output_path}")"
build_path="${output_path}.tmp.$$"
rm -f "${build_path}"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
  rm -f "${build_path}"
}
trap cleanup EXIT

add_context() {
  local source_context="$1"
  local required="$2"
  local ca_data
  local server
  local token
  local ca_file="${tmp_dir}/${source_context//[^a-zA-Z0-9_.-]/_}.crt"

  if ! kubectl config get-contexts "${source_context}" -o name | grep -Fxq "${source_context}"; then
    if [[ "${required}" == "true" ]]; then
      echo "Missing required kubeconfig context: ${source_context}" >&2
      echo "Run the documented gcloud container clusters get-credentials command for this region first." >&2
      exit 1
    fi
    echo "Skipping optional kubeconfig context because it is missing: ${source_context}" >&2
    skipped_contexts+=("${source_context} (missing)")
    return 0
  fi

  if ! kubectl --context "${source_context}" get namespace "${namespace}" --request-timeout=10s >/dev/null 2>&1; then
    if [[ "${required}" == "true" ]]; then
      echo "Required kubeconfig context is unreachable: ${source_context}" >&2
      echo "Refresh it with the documented gcloud container clusters get-credentials command." >&2
      exit 1
    fi
    echo "Skipping optional kubeconfig context because it is unreachable: ${source_context}" >&2
    skipped_contexts+=("${source_context} (unreachable)")
    return 0
  fi

  if ! kubectl --context "${source_context}" apply -f "${rbac_manifest}"; then
    if [[ "${required}" == "true" ]]; then
      echo "Failed to apply regional allocator RBAC in required context: ${source_context}" >&2
      exit 1
    fi
    echo "Skipping optional kubeconfig context because RBAC apply failed: ${source_context}" >&2
    skipped_contexts+=("${source_context} (rbac failed)")
    return 0
  fi

  for _ in $(seq 1 30); do
    token="$(kubectl --context "${source_context}" get secret "${token_secret}" -n "${namespace}" -o jsonpath='{.data.token}' 2>/dev/null | base64 -d || true)"
    if [[ -n "${token}" ]]; then
      break
    fi
    sleep 1
  done

  if [[ -z "${token:-}" ]]; then
    if [[ "${required}" == "true" ]]; then
      echo "Token Secret ${namespace}/${token_secret} was not populated in required context ${source_context}" >&2
      exit 1
    fi
    echo "Skipping optional kubeconfig context because allocator token is unavailable: ${source_context}" >&2
    skipped_contexts+=("${source_context} (token unavailable)")
    return 0
  fi

  server="$(kubectl --context "${source_context}" config view --minify --flatten --raw -o jsonpath='{.clusters[0].cluster.server}')"
  ca_data="$(kubectl --context "${source_context}" config view --minify --flatten --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
  if [[ -z "${server}" || -z "${ca_data}" ]]; then
    if [[ "${required}" == "true" ]]; then
      echo "Required context ${source_context} is missing an API server or embedded cluster CA" >&2
      exit 1
    fi
    echo "Skipping optional kubeconfig context because API server or CA data is missing: ${source_context}" >&2
    skipped_contexts+=("${source_context} (invalid context data)")
    return 0
  fi

  printf '%s' "${ca_data}" | base64 -d > "${ca_file}"
  kubectl config --kubeconfig="${build_path}" set-cluster "${source_context}" \
    --server="${server}" \
    --certificate-authority="${ca_file}" \
    --embed-certs=true >/dev/null
  kubectl config --kubeconfig="${build_path}" set-credentials "${source_context}" --token="${token}" >/dev/null
  kubectl config --kubeconfig="${build_path}" set-context "${source_context}" \
    --cluster="${source_context}" \
    --user="${source_context}" \
    --namespace="${namespace}" >/dev/null
  included_contexts+=("${source_context}")
}

for context_name in "${required_contexts[@]}"; do
  add_context "${context_name}" "true"
done

for context_name in "${optional_contexts[@]}"; do
  add_context "${context_name}" "false"
done

kubectl config --kubeconfig="${build_path}" use-context "${south_america_context}" >/dev/null
chmod 600 "${build_path}"

if [[ ! -s "${build_path}" ]]; then
  echo "Generated kubeconfig is missing or empty: ${build_path}" >&2
  exit 1
fi

for context_name in "${required_contexts[@]}"; do
  if ! kubectl config --kubeconfig="${build_path}" get-contexts "${context_name}" -o name | grep -Fxq "${context_name}"; then
    echo "Generated kubeconfig is missing required South America context: ${context_name}" >&2
    exit 1
  fi
done

mv "${build_path}" "${output_path}"
chmod 600 "${output_path}"

cat <<EOF
Created least-privilege regional allocator kubeconfig:
  ${output_path}

Included contexts:
$(printf '  %s\n' "${included_contexts[@]}")

Skipped optional contexts:
$(if [[ ${#skipped_contexts[@]} -eq 0 ]]; then printf '  none\n'; else printf '  %s\n' "${skipped_contexts[@]}"; fi)

Canonical repo-relative path:
  scripts/.generated/xonotic-multicluster.kubeconfig
EOF
