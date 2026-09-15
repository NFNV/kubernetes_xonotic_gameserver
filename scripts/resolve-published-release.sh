#!/usr/bin/env bash
set -euo pipefail

for command in curl jq; do
  if ! command -v "${command}" >/dev/null 2>&1; then
    echo "${command} is required to resolve a published release." >&2
    exit 1
  fi
done

repository="NFNV/kubernetes_xonotic_gameserver"
requested_sha="${1:-${XONOTIC_RELEASE_SHA:-}}"
sha_regex='^[0-9a-f]{40}$'

curl_release() {
  curl -fsS --retry 2 --retry-max-time 45 --connect-timeout 10 --max-time 30 "$@"
}

if [[ -n "${requested_sha}" ]]; then
  if [[ ! "${requested_sha}" =~ ${sha_regex} ]]; then
    echo "XONOTIC_RELEASE_SHA must be a full lowercase 40-character Git SHA." >&2
    exit 1
  fi
  release_sha="${requested_sha}"
  echo "Using explicitly selected release ${release_sha}." >&2
else
  runs_url="https://api.github.com/repos/${repository}/actions/workflows/publish-images.yml/runs?branch=master&event=workflow_run&status=success&per_page=30"
  if ! runs_json="$(curl_release "${runs_url}")"; then
    echo "Could not query successful master image publications. Set XONOTIC_RELEASE_SHA to a published full SHA to retry." >&2
    exit 1
  fi
  if ! release_sha="$(jq -er '.workflow_runs | map(select(.event == "workflow_run" and .conclusion == "success" and ((.head_sha // "") | test("^[0-9a-f]{40}$")))) | .[0].head_sha // empty' <<<"${runs_json}")"; then
    echo "No successful automatic master image publication was found. Set XONOTIC_RELEASE_SHA to a published full SHA." >&2
    exit 1
  fi
  echo "Latest successful master image publication: ${release_sha}." >&2
fi

manifest_accept='application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'
for image in xonotic-allocator-backend xonotic-allocator-frontend xonotic-server; do
  token_url="https://ghcr.io/token?service=ghcr.io&scope=repository:nfnv/${image}:pull"
  if ! token_json="$(curl_release "${token_url}")"; then
    echo "Could not request a GHCR pull token for ${image}." >&2
    exit 1
  fi
  if ! token="$(jq -er '.token // empty' <<<"${token_json}")"; then
    echo "GHCR did not return a pull token for ${image}." >&2
    exit 1
  fi
  manifest_url="https://ghcr.io/v2/nfnv/${image}/manifests/sha-${release_sha}"
  if ! curl_release -I -o /dev/null \
    -H "Authorization: Bearer ${token}" \
    -H "Accept: ${manifest_accept}" \
    "${manifest_url}"; then
    echo "Required image ghcr.io/nfnv/${image}:sha-${release_sha} is missing or inaccessible. No infrastructure was changed." >&2
    exit 1
  fi
done

version_url="https://raw.githubusercontent.com/${repository}/${release_sha}/VERSION"
if ! release_version="$(curl_release "${version_url}" | tr -d '[:space:]')"; then
  echo "Could not read VERSION at release ${release_sha}." >&2
  exit 1
fi
version_regex='^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$'
if [[ ! "${release_version}" =~ ${version_regex} ]]; then
  echo "Release ${release_sha} has an invalid VERSION value." >&2
  exit 1
fi

jq -n --arg sha "${release_sha}" --arg version "${release_version}" \
  '{sha: $sha, version: $version}'
