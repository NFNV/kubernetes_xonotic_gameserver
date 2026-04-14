#!/usr/bin/env bash
set -euo pipefail

readonly install_dir="${XONOTIC_HOME:-/opt/xonotic}"
readonly user_dir="${XONOTIC_USERDIR:-${HOME}/.xonotic}"
readonly data_dir="${user_dir}/data"
readonly template_cfg="/opt/xonotic-config/server.cfg"
readonly server_cfg="${data_dir}/server.cfg"
readonly autoexec_cfg="${data_dir}/server.autoexec.cfg"

notify_agones_ready() {
  local sdk_http_port="${AGONES_SDK_HTTP_PORT:-}"
  local ready_delay="${XONOTIC_AGONES_READY_DELAY_SECONDS:-10}"
  local ready_attempts="${XONOTIC_AGONES_READY_ATTEMPTS:-30}"
  local attempt

  if [[ "${XONOTIC_AGONES_READY_ENABLE:-0}" != "1" ]]; then
    return 0
  fi

  if [[ -z "${sdk_http_port}" ]]; then
    echo "XONOTIC_AGONES_READY_ENABLE=1 but AGONES_SDK_HTTP_PORT is not set" >&2
    return 1
  fi

  sleep "${ready_delay}"

  for ((attempt = 1; attempt <= ready_attempts; attempt++)); do
    if curl --fail --silent --show-error \
      -H "Content-Type: application/json" \
      -X POST \
      -d "{}" \
      "http://127.0.0.1:${sdk_http_port}/ready" >/dev/null; then
      echo "Agones Ready sent on attempt ${attempt}" >&2
      return 0
    fi

    sleep 1
  done

  echo "Failed to send Agones Ready after ${ready_attempts} attempts" >&2
  return 1
}

escape_cvar_string() {
  local value="${1}"

  value="${value//$'\r'/}"
  value="${value//$'\n'/ }"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"

  printf '%s' "${value}"
}

mkdir -p "${data_dir}"

if [[ ! -f "${server_cfg}" ]]; then
  cp "${template_cfg}" "${server_cfg}"
fi

port="${XONOTIC_PORT:-26000}"
hostname="${XONOTIC_HOSTNAME:-Xonotic Dedicated Server}"
maxplayers="${XONOTIC_MAXPLAYERS:-12}"
public="${XONOTIC_PUBLIC:-0}"
motd="${XONOTIC_MOTD:-Welcome to the Xonotic dedicated server}"
log_file="${XONOTIC_LOG_FILE:-server.log}"
map_name="${XONOTIC_MAP:-}"
rcon_password="${XONOTIC_RCON_PASSWORD:-}"
extra_cfg="${XONOTIC_EXTRA_CFG:-}"

escaped_hostname="$(escape_cvar_string "${hostname}")"
escaped_motd="$(escape_cvar_string "${motd}")"
escaped_log_file="$(escape_cvar_string "${log_file}")"
escaped_rcon_password="$(escape_cvar_string "${rcon_password}")"

cat > "${autoexec_cfg}" <<EOF
// Generated at container start. Mount your own server.cfg if you want full control.
// XONOTIC_EXTRA_CFG is a convenience override for local debugging, not the primary config mechanism.
port ${port}
hostname "${escaped_hostname}"
maxplayers ${maxplayers}
sv_public ${public}
sv_motd "${escaped_motd}"
log_file "${escaped_log_file}"
EOF

if [[ -n "${rcon_password}" ]]; then
  printf 'rcon_password "%s"\n' "${escaped_rcon_password}" >> "${autoexec_cfg}"
fi

if [[ -n "${extra_cfg}" ]]; then
  printf '\n// Additional runtime config\n%s\n' "${extra_cfg}" >> "${autoexec_cfg}"
fi

cd "${install_dir}"

cmd=(
  "./xonotic-linux64-dedicated"
  "+serverconfig" "server.cfg"
  "+exec" "server.autoexec.cfg"
)

if [[ -n "${map_name}" ]]; then
  cmd+=("+map" "${map_name}")
fi

cmd+=("$@")

if [[ "${XONOTIC_AGONES_READY_ENABLE:-0}" == "1" ]]; then
  (
    if ! notify_agones_ready; then
      echo "Agones Ready notification did not succeed; the GameServer may remain in Scheduled" >&2
    fi
  ) &
fi

exec "${cmd[@]}"
