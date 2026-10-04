#!/usr/bin/env bash
# DEV ONLY: call the API as a seeded user, e.g. to try the permission matrix by hand.
#
#   scripts/api.sh eddie POST /links '{"target_url": "https://example.com"}'
#   scripts/api.sh victor GET /links/<id>
#   scripts/api.sh - GET /links            # "-" sends no token
#
# Prints "HTTP <status>" to stderr and the response body (pretty-printed by jq) to stdout, so
# `LINK=$(scripts/api.sh ...)` captures only the JSON. Exits 0 for any HTTP status. PATH is relative
# to /api/v1; API_URL overrides http://localhost:8000. Needs jq and the running stack (`make up`).
set -euo pipefail
MYDIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit; pwd -P)
readonly MYDIR
#
##############################################################################
#
# Load some utilities
#
readonly THE_UTILS=( "common" "api" )

for utility in "${THE_UTILS[@]}"; do
  if [[ -r "${MYDIR}/utils_${utility}.sh" ]]; then
    source "${MYDIR}/utils_${utility}.sh"
  else
    >&2 echo -e "\e[31mERROR:\e[39m Unable to load library 'utils_${utility}.sh'"
    exit 1
  fi
done
#
##############################################################################
#
if [[ $# -lt 3 || $# -gt 4 ]]; then
  sed -n 's/^#   //p' "$0" >&2
  exit 2
fi
if ! command -v jq >/dev/null; then
  puterr "scripts/api.sh needs jq (brew install jq)"
  exit 1
fi

api_request "$@" || exit  # curl has already said why (e.g. the stack isn't up)

echo "HTTP $API_STATUS" >&2
if [[ -n $API_BODY ]]; then
  jq . <<<"$API_BODY" 2>/dev/null || printf '%s\n' "$API_BODY"
fi
