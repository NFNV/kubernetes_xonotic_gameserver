#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/verify-tournament-map-mode.sh [--backend-url URL] [--experimental] [--keep-server] <mode> <map>

Verifies one tournament map/mode pair through the persisted tournament allocation flow.

Defaults:
  BACKEND_URL=http://127.0.0.1:18080

Notes:
  --experimental sends allow_experimental_game_config=true. The backend must be
  deployed with XONOTIC_ENABLE_EXPERIMENTAL_GAME_CONFIG=1 for disabled candidate
  modes such as ctf, duel, ca, dom, and kh.
  The script releases the allocated server by default so failed probes do not
  consume Agones capacity.
EOF
}

backend_url="${BACKEND_URL:-http://127.0.0.1:18080}"
experimental=0
keep_server=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --backend-url)
      backend_url="${2:-}"
      shift 2
      ;;
    --experimental)
      experimental=1
      shift
      ;;
    --keep-server)
      keep_server=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

mode="${1:-}"
map_name="${2:-}"

if [[ -z "${mode}" || -z "${map_name}" ]]; then
  usage >&2
  exit 2
fi

for dependency in curl jq; do
  if ! command -v "${dependency}" >/dev/null 2>&1; then
    echo "Missing dependency: ${dependency}" >&2
    exit 2
  fi
done

backend_url="${backend_url%/}"
probe_suffix="$(date +%Y%m%d%H%M%S)"

post_json() {
  local path="$1"
  local payload="$2"
  curl -fsS \
    -X POST \
    -H "content-type: application/json" \
    -d "${payload}" \
    "${backend_url}${path}"
}

echo "Creating disposable tournament verification records for ${mode}/${map_name}..."

tournament_payload="$(jq -n --arg name "Map/mode verification ${mode}/${map_name} ${probe_suffix}" '{name: $name, description: "Disposable compatibility probe"}')"
tournament_json="$(post_json "/tournaments" "${tournament_payload}")"
tournament_id="$(jq -r '.id' <<<"${tournament_json}")"

team_a_json="$(post_json "/tournaments/${tournament_id}/teams" "$(jq -n '{name: "Probe Alpha", tag: "ALPHA", seed: 1}')")"
team_b_json="$(post_json "/tournaments/${tournament_id}/teams" "$(jq -n '{name: "Probe Bravo", tag: "BRAVO", seed: 2}')")"
team_a_id="$(jq -r '.id' <<<"${team_a_json}")"
team_b_id="$(jq -r '.id' <<<"${team_b_json}")"

round_json="$(post_json "/tournaments/${tournament_id}/rounds" "$(jq -n '{name: "Verification Round", round_order: 1}')")"
round_id="$(jq -r '.id' <<<"${round_json}")"

match_payload="$(
  jq -n \
    --arg round_id "${round_id}" \
    --arg team_a_id "${team_a_id}" \
    --arg team_b_id "${team_b_id}" \
    --arg mode "${mode}" \
    --arg map "${map_name}" \
    --argjson experimental "${experimental}" \
    '{
      round_id: $round_id,
      team_a_id: $team_a_id,
      team_b_id: $team_b_id,
      requested_game_mode: $mode,
      requested_map: $map
    } + (if $experimental then {
      verification_probe: true,
      allow_experimental_game_config: true
    } else {} end)'
)"
match_json="$(post_json "/tournaments/${tournament_id}/matches" "${match_payload}")"
match_id="$(jq -r '.id' <<<"${match_json}")"

allocation_payload="$(
  jq -n \
    --argjson experimental "${experimental}" \
    'if $experimental then {allow_experimental_game_config: true} else {} end'
)"

echo "Allocating and verifying ${mode}/${map_name} for match ${match_id}..."
allocation_json="$(post_json "/tournaments/${tournament_id}/matches/${match_id}/allocate-server" "${allocation_payload}")"

echo "${allocation_json}" | jq '{
  match_id: .match.id,
  match_status: .match.status,
  endpoint: .assignment.endpoint,
  verified: .configuration.verified,
  expected_map: .configuration.expected_map,
  actual_map: .configuration.actual_map,
  expected_game_mode: .configuration.expected_game_mode,
  actual_game_mode: .configuration.actual_game_mode,
  warning: .warning
}'

verified="$(jq -r '.configuration.verified == true' <<<"${allocation_json}")"
actual_map="$(jq -r '.configuration.actual_map // ""' <<<"${allocation_json}")"
actual_mode="$(jq -r '.configuration.actual_game_mode // ""' <<<"${allocation_json}")"

if [[ "${keep_server}" != "1" ]]; then
  echo "Releasing verification server assignment..."
  post_json "/tournaments/${tournament_id}/matches/${match_id}/release-server" '{}' >/dev/null
else
  echo "Keeping server assignment active for manual inspection."
fi

if [[ "${verified}" == "true" && "${actual_map}" == "${map_name}" && "${actual_mode}" == "${mode}" ]]; then
  echo "VERIFIED ${mode}/${map_name}"
else
  echo "FAILED ${mode}/${map_name}: getstatus reported mode=${actual_mode:-unknown} map=${actual_map:-unknown}" >&2
  exit 1
fi
