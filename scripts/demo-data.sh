#!/usr/bin/env bash
# DEV ONLY: fill the running stack with links, clicks and moderation history, so signing in as
# sam (support) has something to look at.
#
#   scripts/demo-data.sh        # or: make demo-data   (needs `make up` first)
#
# Every run adds new links; `make down && make up` starts from empty. API_URL overrides the API.
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
cd "$(dirname "$0")/.."

# Pull a string field out of the API's compact JSON (avoids a jq dependency).
field() { sed -n "s/.*\"$1\":\"\([^\"]*\)\".*/\1/p" <<<"$2"; }

# call USER METHOD PATH [JSON] -> prints the response body; stops the script on any non-2xx.
call() {
  api_request "$@" || exit  # set -e doesn't reach inside $(...); curl has said why
  if [[ $API_STATUS != 2?? ]]; then
    puterr "$2 $3 as $1 returned HTTP $API_STATUS: $API_BODY"
    exit 1
  fi
  printf '%s' "$API_BODY"
}

# create USER TARGET_URL -> prints the link JSON
create() { call "$1" POST /links "{\"target_url\": \"$2\"}"; }

# click CODE COUNT [REFERER...]: follow the short link COUNT times, cycling through the referers
# ("-" means no Referer header). Each 302 becomes a click event for the processor.
click() {
  local code=$1 count=$2 i ref
  shift 2
  local refs=("${@:--}")
  for ((i = 0; i < count; i++)); do
    ref=${refs[i % ${#refs[@]}]}
    if [[ $ref == - ]]; then
      curl -s -o /dev/null "$API_URL/$code"
    else
      curl -s -o /dev/null -H "Referer: https://$ref/" "$API_URL/$code"
    fi
  done
}

MATT_AND_BOB=(
  "https://www.neu.edu"
  "https://www.khoury.northeastern.edu/"
  "https://www.iq.harvard.edu/"
  "https://www.iq.harvard.edu/research-computing"
  "https://extension.harvard.edu/"
  "https://github.com/matthew-cox/PatchMatch-CUDA"
)

putinfo "Fetching tokens for eddie, erin and sam..."
# Up front, so the create calls below (run in $(...) subshells) reuse them.
api_login eddie erin sam
putsuccess "Tokens fetched"

for i in "${!MATT_AND_BOB[@]}"; do
  putinfo "Creating special link #$((i + 1))..."
  the_link=$(create eddie "${MATT_AND_BOB[i]}")
  click "$(field code "$the_link")" 17 -
  putsuccess "Link created and clicked: $(field short_url "$the_link")"
done

exit

putinfo "Creating links..."
sale=$(create eddie "https://example.com/spring-sale?utm_source=newsletter")
docs=$(create eddie "https://docs.example.com/getting-started")
webinar=$(create eddie "https://example.org/webinar/signup")
old=$(create eddie "https://example.net/campaigns/2025-autumn")
phish=$(create eddie "https://secure-login.example-payments.net/account/verify?session=8f3a2c")
careers=$(create erin "https://example.com/careers")
prize=$(create erin "https://bit-prize.example.info/claim-your-reward")
survey=$(create erin "https://forms.example.com/customer-survey")
putsuccess "Links created"

putinfo "Generating clicks..."
click "$(field code "$sale")" 40 news.ycombinator.com t.co www.linkedin.com -
click "$(field code "$docs")" 25 www.google.com github.com -
click "$(field code "$webinar")" 12 www.linkedin.com
click "$(field code "$old")" 3
click "$(field code "$careers")" 15 www.linkedin.com www.google.com
click "$(field code "$survey")" 8 -
# The link the walkthrough is about: a burst of clicks arriving from webmail.
click "$(field code "$phish")" 90 mail.google.com outlook.live.com mail.yahoo.com -
click "$(field code "$prize")" 30 t.co www.facebook.com

putinfo "Adding owner and moderation activity..."
call eddie PATCH "/links/$(field id "$old")" '{"is_active": false}' >/dev/null
call sam POST "/links/$(field id "$prize")/block" \
  '{"reason": "Prize scam reported by two customers (ticket DEMO-101)"}' >/dev/null
call sam POST "/links/$(field id "$survey")/block" \
  '{"reason": "Suspected credential harvesting (ticket DEMO-102)"}' >/dev/null
call sam POST "/links/$(field id "$survey")/unblock" >/dev/null
# Visitors still following the blocked link get 410 and aren't counted as clicks.
click "$(field code "$prize")" 5
putsuccess "Owner and moderation activity added"

cat <<EOF

Done. Clicks reach the stats within a few seconds (the click-processor rolls them up).

Walkthrough: sign in to the admin UI at http://localhost:8001 as sam (support, password: password).
  - Reported phishing link:    $(field short_url "$phish")
    Search for its code ($(field code "$phish")), check its traffic, then block it.
  - Already blocked:            $(field short_url "$prize")
  - Blocked, then unblocked:    $(field short_url "$survey")  (see its History)
  - Disabled by its owner:      $(field short_url "$old")
EOF
