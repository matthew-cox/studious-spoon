#!/usr/bin/env bash
#
# DEV ONLY: call the shortener API as a seeded user (sourced by scripts/api.sh, scripts/demo-data.sh)
#
#   api_login eddie sam                       # optional: fetch tokens up front (see below)
#   api_request eddie POST /links '{"target_url": "https://example.com"}'
#   echo "$API_STATUS $API_BODY"
#
# API_URL overrides http://localhost:8000. Works with macOS's bash 3.2 (no associative arrays).
#
##############################################################################
#
_API_LIB_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
API_URL="${API_URL:-http://localhost:8000}"

#
##############################################################################
#
# api_login USER...
#
# Fetch and cache a token for each user (scripts/token takes ~0.5 s). api_request fetches on first
# use anyway, but a cache filled inside $(...) is lost with the subshell, so call this first when
# requests run in command substitutions.
#
api_login() {
  local user var token
  for user in "$@"; do
    var="_API_TOKEN_${user//[^a-zA-Z0-9]/_}"
    if [[ -z "${!var:-}" ]]; then
      token=$("${_API_LIB_DIR}/token" "$user") || return
      printf -v "$var" '%s' "$token"
    fi
  done
}

#
##############################################################################
#
# api_request USER METHOD PATH [JSON]
#
# PATH is relative to /api/v1. USER "-" sends no token. Sets API_STATUS (e.g. 201) and API_BODY
# (raw response, may be empty). Returns non-zero only if the API couldn't be reached; any HTTP
# status counts as success, so callers check API_STATUS.
#
api_request() {
  local user=$1 method=$2 path=$3 body=${4:-} var out
  local args=(-sS -w '\n%{http_code}' -X "$method" "${API_URL}/api/v1${path}")

  if [[ $user != "-" ]]; then
    api_login "$user" || return
    var="_API_TOKEN_${user//[^a-zA-Z0-9]/_}"
    args+=(-H "Authorization: Bearer ${!var}")
  fi
  if [[ -n $body ]]; then
    args+=(-H 'Content-Type: application/json' -d "$body")
  fi

  out=$(curl "${args[@]}") || return
  API_STATUS=${out##*$'\n'}
  API_BODY=${out%"$API_STATUS"}
  API_BODY=${API_BODY%$'\n'}
}
