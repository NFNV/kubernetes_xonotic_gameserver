#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/up-region.sh <europe|north-america>
  ./scripts/down-region.sh <europe|north-america>
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 1
fi

region="$1"
case "${region}" in
  europe | north-america) ;;
  *)
    usage
    exit 1
    ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
env_file="${script_dir}/env.sh"
infra_dir="${repo_root}/infra"
tfvars_file="${infra_dir}/regions/${region}.tfvars"
plan_file="${TMPDIR:-/tmp}/xonotic-${region}-destroy.tfplan"

if [[ -f "${env_file}" ]]; then
  # shellcheck disable=SC1090
  source "${env_file}"
fi

: "${GCP_PROJECT_ID:?GCP_PROJECT_ID must be set}"

if [[ ! -f "${tfvars_file}" ]]; then
  echo "Missing Terraform variables file: ${tfvars_file}" >&2
  exit 1
fi

restore_workspace() {
  if [[ -n "${previous_workspace:-}" ]]; then
    terraform -chdir="${infra_dir}" workspace select "${previous_workspace}" >/dev/null 2>&1 || true
  fi
}

cat <<EOF
Selected region: ${region}
Terraform tfvars: ${tfvars_file}
Terraform project: ${GCP_PROJECT_ID}

Destructive warning: this destroys the GKE/firewall Terraform resources tracked in the
${region} workspace. It does not intentionally destroy any other region workspace.
EOF

terraform -chdir="${infra_dir}" init
previous_workspace="$(terraform -chdir="${infra_dir}" workspace show 2>/dev/null || printf 'default')"
trap restore_workspace EXIT

if ! terraform -chdir="${infra_dir}" workspace select "${region}"; then
  echo "Terraform workspace '${region}' does not exist. Nothing to destroy."
  exit 0
fi

active_workspace="$(terraform -chdir="${infra_dir}" workspace show)"
echo "Terraform workspace: ${active_workspace}"

terraform -chdir="${infra_dir}" plan \
  -destroy \
  -var-file="regions/${region}.tfvars" \
  -var="project_id=${GCP_PROJECT_ID}" \
  -out="${plan_file}"

if [[ "${XONOTIC_AUTO_APPROVE:-0}" != "1" ]]; then
  echo
  read -r -p "Type 'destroy ${region}' to destroy this regional infrastructure: " confirmation
  if [[ "${confirmation}" != "destroy ${region}" ]]; then
    echo "Aborted. No infrastructure was changed."
    exit 1
  fi
fi

terraform -chdir="${infra_dir}" apply "${plan_file}"
