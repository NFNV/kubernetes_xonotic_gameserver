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

namespace="xonotic-allocator-backend"
secret_name="xonotic-multicluster-kubeconfig"
expected_south_america_context="gke_${GCP_PROJECT_ID}_southamerica-west1-a_xonotic-mvp"
expected_europe_context="gke_${GCP_PROJECT_ID}_europe-west1-b_xonotic-eu"
expected_north_america_context="gke_${GCP_PROJECT_ID}_us-central1-a_xonotic-na"
south_america_context="${XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT:-${expected_south_america_context}}"
europe_context="${XONOTIC_EUROPE_KUBE_CONTEXT:-${expected_europe_context}}"
north_america_context="${XONOTIC_NORTH_AMERICA_KUBE_CONTEXT:-${expected_north_america_context}}"
canonical_path="${script_dir}/.generated/xonotic-multicluster.kubeconfig"
if [[ "${XONOTIC_MULTICLUSTER_KUBECONFIG+x}" == "x" && -z "${XONOTIC_MULTICLUSTER_KUBECONFIG}" ]]; then
  echo "XONOTIC_MULTICLUSTER_KUBECONFIG is set but empty." >&2
  echo "Unset it or set it to scripts/.generated/xonotic-multicluster.kubeconfig." >&2
  exit 1
fi
configured_path="${XONOTIC_MULTICLUSTER_KUBECONFIG:-${canonical_path}}"
primary_context="${south_america_context}"
required_contexts=(
  "${south_america_context}"
  "${europe_context}"
  "${north_america_context}"
)

if [[ "${south_america_context}" != "${expected_south_america_context}" \
  || "${europe_context}" != "${expected_europe_context}" \
  || "${north_america_context}" != "${expected_north_america_context}" ]]; then
  echo "Configured regional context names do not match the expected GKE contexts for project ${GCP_PROJECT_ID}." >&2
  exit 1
fi

if [[ -z "${configured_path}" ]]; then
  echo "XONOTIC_MULTICLUSTER_KUBECONFIG resolved to an empty path." >&2
  echo "Run ./scripts/build-multicluster-kubeconfig.sh first." >&2
  exit 1
fi

if [[ "${configured_path}" = /* ]]; then
  kubeconfig_path="${configured_path}"
else
  kubeconfig_path="${repo_root}/${configured_path#./}"
fi

if [[ ! -e "${kubeconfig_path}" ]]; then
  echo "Multicluster kubeconfig does not exist: ${kubeconfig_path}" >&2
  echo "Run ./scripts/build-multicluster-kubeconfig.sh first." >&2
  exit 1
fi

if [[ ! -s "${kubeconfig_path}" ]]; then
  echo "Multicluster kubeconfig is empty: ${kubeconfig_path}" >&2
  echo "Rebuild it with ./scripts/build-multicluster-kubeconfig.sh." >&2
  exit 1
fi

for context_name in "${required_contexts[@]}"; do
  if ! kubectl config --kubeconfig="${kubeconfig_path}" get-contexts "${context_name}" -o name | grep -Fxq "${context_name}"; then
    echo "Multicluster kubeconfig is missing required context: ${context_name}" >&2
    echo "Refresh all regional credentials and rebuild the kubeconfig." >&2
    exit 1
  fi
done

if ! kubectl --context "${primary_context}" get namespace "${namespace}" --request-timeout=10s >/dev/null 2>&1; then
  echo "Primary namespace ${namespace} is unavailable in context ${primary_context}." >&2
  echo "Bring up the South America control plane before applying this Secret." >&2
  exit 1
fi

kubectl --context "${primary_context}" create secret generic "${secret_name}" \
  -n "${namespace}" \
  --from-file=config="${kubeconfig_path}" \
  --from-literal=XONOTIC_SOUTH_AMERICA_KUBE_CONTEXT="${south_america_context}" \
  --from-literal=XONOTIC_EUROPE_KUBE_CONTEXT="${europe_context}" \
  --from-literal=XONOTIC_NORTH_AMERICA_KUBE_CONTEXT="${north_america_context}" \
  --dry-run=client \
  -o yaml \
  | kubectl --context "${primary_context}" apply -f -

echo "Applied ${namespace}/${secret_name} from ${kubeconfig_path}."
