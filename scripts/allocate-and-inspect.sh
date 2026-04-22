#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/.." && pwd)"

gameserver_namespace="xonotic-agones"
allocation_manifest="${repo_root}/platform/agones/manifests/xonotic-gameserverallocation.yaml"

allocation_ref="$(kubectl create -f "${allocation_manifest}" -o name)"
allocation_name="${allocation_ref##*/}"
gameserver_name="$(kubectl get "${allocation_ref}" -n "${gameserver_namespace}" -o jsonpath='{.status.gameServerName}')"
address="$(kubectl get "${allocation_ref}" -n "${gameserver_namespace}" -o jsonpath='{.status.address}')"
port="$(kubectl get "${allocation_ref}" -n "${gameserver_namespace}" -o jsonpath='{.status.ports[0].port}')"
pod_name="$(kubectl get pod "${gameserver_name}" -n "${gameserver_namespace}" -o jsonpath='{.metadata.name}' 2>/dev/null || true)"

if [[ -z "${pod_name}" ]]; then
  pod_name="${gameserver_name}"
fi

echo "allocation_request_name=${allocation_name}"
echo "allocated_game_server_name=${gameserver_name}"
echo "pod_name=${pod_name}"
echo "endpoint=${address}:${port}"
kubectl get gameserver "${gameserver_name}" -n "${gameserver_namespace}" -o custom-columns=NAME:.metadata.name,STATE:.status.state,ADDRESS:.status.address,PORT:.status.ports[0].port,NODE:.status.nodeName
