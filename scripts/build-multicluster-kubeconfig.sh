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

default_south_america_source_context="gke_${GCP_PROJECT_ID}_southamerica-west1-a_xonotic-mvp"
default_europe_source_context="gke_${GCP_PROJECT_ID}_europe-west1-b_xonotic-eu"
default_north_america_source_context="gke_${GCP_PROJECT_ID}_us-central1-a_xonotic-na"
south_america_context="south-america-default"
europe_context="europe-default"
north_america_context="north-america-default"

resolve_source_context() {
  local explicit_source="$1"
  local legacy_context="$2"
  local default_source="$3"
  local generated_context="$4"

  if [[ -n "${explicit_source}" ]]; then
    printf '%s\n' "${explicit_source}"
  elif [[ -n "${legacy_context}" && "${legacy_context}" != "${generated_context}" ]]; then
    # Before canonical aliases, XONOTIC_*_KUBE_CONTEXT named the source gcloud context.
    printf '%s\n' "${legacy_context}"
  else
    printf '%s\n' "${default_source}"
  fi
}

south_america_source_context="$(resolve_source_context \
  "${XONOTIC_SOUTH_AMERICA_SOURCE_KUBE_CONTEXT:-}" \
  "${XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT:-}" \
  "${default_south_america_source_context}" \
  "${south_america_context}")"
europe_source_context="$(resolve_source_context \
  "${XONOTIC_EUROPE_SOURCE_KUBE_CONTEXT:-}" \
  "${XONOTIC_EUROPE_KUBE_CONTEXT:-}" \
  "${default_europe_source_context}" \
  "${europe_context}")"
north_america_source_context="$(resolve_source_context \
  "${XONOTIC_NORTH_AMERICA_SOURCE_KUBE_CONTEXT:-}" \
  "${XONOTIC_NORTH_AMERICA_KUBE_CONTEXT:-}" \
  "${default_north_america_source_context}" \
  "${north_america_context}")"
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
included_contexts=()
included_source_contexts=()
skipped_contexts=()

mkdir -p "$(dirname "${output_path}")"
build_path="${output_path}.tmp.$$"
rm -f "${build_path}"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmp_dir}"
  rm -f "${build_path}"
}
trap cleanup EXIT

context_exists() {
  local kubeconfig_path="$1"
  local context_name="$2"
  local resolved_context

  if [[ -n "${kubeconfig_path}" ]]; then
    resolved_context="$(kubectl config --kubeconfig="${kubeconfig_path}" get-contexts "${context_name}" -o name 2>/dev/null)" || return 1
  else
    resolved_context="$(kubectl config get-contexts "${context_name}" -o name 2>/dev/null)" || return 1
  fi

  [[ "${resolved_context}" == "${context_name}" ]]
}

add_context() {
  local source_context="$1"
  local generated_context="$2"
  local required="$3"
  local ca_data
  local server
  local token
  local ca_file="${tmp_dir}/${generated_context//[^a-zA-Z0-9_.-]/_}.crt"

  if ! context_exists "" "${source_context}"; then
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
  kubectl config --kubeconfig="${build_path}" set-cluster "${generated_context}" \
    --server="${server}" \
    --certificate-authority="${ca_file}" \
    --embed-certs=true >/dev/null
  kubectl config --kubeconfig="${build_path}" set-credentials "${generated_context}" --token="${token}" >/dev/null
  kubectl config --kubeconfig="${build_path}" set-context "${generated_context}" \
    --cluster="${generated_context}" \
    --user="${generated_context}" \
    --namespace="${namespace}" >/dev/null
  included_contexts+=("${generated_context}")
  included_source_contexts+=("${source_context}")
}

add_context "${south_america_source_context}" "${south_america_context}" "true"
add_context "${europe_source_context}" "${europe_context}" "false"
add_context "${north_america_source_context}" "${north_america_context}" "false"

kubectl config --kubeconfig="${build_path}" use-context "${south_america_context}" >/dev/null
chmod 600 "${build_path}"

if [[ ! -s "${build_path}" ]]; then
  echo "Generated kubeconfig is missing or empty: ${build_path}" >&2
  exit 1
fi

for context_name in "${included_contexts[@]}"; do
  if ! context_exists "${build_path}" "${context_name}"; then
    echo "Generated kubeconfig is missing expected regional context: ${context_name}" >&2
    exit 1
  fi
done

generated_context_count="$(kubectl config --kubeconfig="${build_path}" get-contexts -o name | wc -l | tr -d '[:space:]')"
if [[ "${generated_context_count}" -ne "${#included_contexts[@]}" ]]; then
  echo "Generated kubeconfig contains unexpected contexts." >&2
  exit 1
fi

mv "${build_path}" "${output_path}"
chmod 600 "${output_path}"

cat <<EOF
Created least-privilege regional allocator kubeconfig:
  ${output_path}

Included contexts:
$(for index in "${!included_contexts[@]}"; do printf '  %s (source: %s)\n' "${included_contexts[$index]}" "${included_source_contexts[$index]}"; done)

Skipped optional contexts:
$(if [[ ${#skipped_contexts[@]} -eq 0 ]]; then printf '  none\n'; else printf '  %s\n' "${skipped_contexts[@]}"; fi)

Canonical repo-relative path:
  scripts/.generated/xonotic-multicluster.kubeconfig
EOF
