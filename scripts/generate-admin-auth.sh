#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/generate-admin-auth.sh [--username admin] [--password PASSWORD]

Prints export lines for scripts/env.sh.

ADMIN_PASSWORD_HASH format:
  pbkdf2:sha256:<iterations>$<salt>$<hex-digest>

That format is compatible with Werkzeug check_password_hash(), which the
allocator backend uses at login time. The hash contains "$" separators, so
keep the generated single quotes when pasting into scripts/env.sh.
EOF
}

username="${ADMIN_USERNAME:-admin}"
password=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --username)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "--username requires a non-empty value" >&2
        exit 1
      fi
      username="$2"
      shift 2
      ;;
    --password)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "--password requires a non-empty value" >&2
        exit 1
      fi
      password="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "${password}" ]]; then
  read -r -s -p "Admin password: " password
  echo
  read -r -s -p "Confirm admin password: " confirm_password
  echo
  if [[ "${password}" != "${confirm_password}" ]]; then
    echo "Passwords do not match" >&2
    exit 1
  fi
fi

if [[ -z "${password}" ]]; then
  echo "Admin password must not be empty" >&2
  exit 1
fi

python3 - "${username}" "${password}" <<'PY'
import hashlib
import secrets
import shlex
import sys

username = sys.argv[1]
password = sys.argv[2]
iterations = 600_000
salt = secrets.token_urlsafe(16)
digest = hashlib.pbkdf2_hmac(
    "sha256",
    password.encode("utf-8"),
    salt.encode("utf-8"),
    iterations,
).hex()
session_secret = secrets.token_urlsafe(48)
password_hash = f"pbkdf2:sha256:{iterations}${salt}${digest}"

print("# Paste these lines into scripts/env.sh.")
print("# ADMIN_PASSWORD_HASH is Werkzeug-compatible: pbkdf2:sha256:<iterations>$<salt>$<hex-digest>")
print("# Keep the single quotes around ADMIN_PASSWORD_HASH so shell does not expand '$' separators.")
print(f"export ADMIN_USERNAME={shlex.quote(username)}")
print(f"export ADMIN_PASSWORD_HASH={shlex.quote(password_hash)}")
print(f"export ADMIN_SESSION_SECRET={shlex.quote(session_secret)}")
PY
