#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"
release_sha="${1:?full release SHA required}"
release_version="${2:?release version required}"
output_dir="${3:?output directory required}"
cluster_name="${4:?cluster name required}"
deployed_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

sha_regex='^[0-9a-f]{40}$'
if [[ ! "${release_sha}" =~ ${sha_regex} ]]; then
  echo "Release SHA must be a full lowercase 40-character Git SHA." >&2
  exit 1
fi
version_regex='^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$'
if [[ ! "${release_version}" =~ ${version_regex} ]]; then
  echo "Release version is not valid SemVer." >&2
  exit 1
fi
label_version="${release_version//+/_}"
if (( ${#label_version} > 63 )); then
  echo "Release version is too long for a Kubernetes label." >&2
  exit 1
fi
if [[ ! -d "${output_dir}" ]]; then
  echo "Output directory ${output_dir} does not exist." >&2
  exit 1
fi

cp -R "${repo_root}/platform/agones/manifests" "${output_dir}/agones"
cp -R "${repo_root}/platform/allocator-backend/manifests" "${output_dir}/backend"
cp -R "${repo_root}/platform/allocator-frontend/manifests" "${output_dir}/frontend"

sed -i.bak "s/newTag: connectivity-checkpoint/newTag: sha-${release_sha}/" "${output_dir}/agones/kustomization.yaml"
sed -i.bak "s/newTag: allocator-backend/newTag: sha-${release_sha}/" "${output_dir}/backend/kustomization.yaml"
sed -i.bak "s/newTag: allocator-frontend/newTag: sha-${release_sha}/" "${output_dir}/frontend/kustomization.yaml"

cat >"${output_dir}/backend/release-metadata-patch.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xonotic-allocator-backend
  namespace: xonotic-allocator-backend
spec:
  template:
    metadata:
      labels:
        app.kubernetes.io/version: "${label_version}"
      annotations:
        platform.xonotic/version: "${release_version}"
        platform.xonotic/revision: "${release_sha}"
        platform.xonotic/deployed-at: "${deployed_at}"
        platform.xonotic/environment: local
        platform.xonotic/cluster: "${cluster_name}"
    spec:
      containers:
        - name: backend
          env:
            - name: DEPLOYED_AT
              value: "${deployed_at}"
            - name: DEPLOYMENT_ENVIRONMENT
              value: local
            - name: CLUSTER_NAME
              value: "${cluster_name}"
EOF

cat >"${output_dir}/frontend/release-metadata-patch.yaml" <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: xonotic-allocator-frontend
  namespace: xonotic-allocator-backend
spec:
  template:
    metadata:
      labels:
        app.kubernetes.io/version: "${label_version}"
      annotations:
        platform.xonotic/version: "${release_version}"
        platform.xonotic/revision: "${release_sha}"
        platform.xonotic/deployed-at: "${deployed_at}"
        platform.xonotic/environment: local
        platform.xonotic/cluster: "${cluster_name}"
EOF

for component in backend frontend; do
  cat >>"${output_dir}/${component}/kustomization.yaml" <<'EOF'

patches:
  - path: release-metadata-patch.yaml
EOF
done

for component in agones backend frontend; do
  kubectl kustomize "${output_dir}/${component}" >"${output_dir}/${component}-rendered.yaml"
done

for pair in \
  "agones:xonotic-server" \
  "backend:xonotic-allocator-backend" \
  "frontend:xonotic-allocator-frontend"; do
  component="${pair%%:*}"
  image="${pair#*:}"
  if ! grep -F "ghcr.io/nfnv/${image}:sha-${release_sha}" "${output_dir}/${component}-rendered.yaml" >/dev/null; then
    echo "Rendered ${component} manifest does not use the selected release image." >&2
    exit 1
  fi
done

echo "Prepared primary manifests for v${release_version} (${release_sha})."
