#!/usr/bin/env bash
#
# Small script to attempt and get colima setup from scratch
#
MYDIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" || exit; pwd -P)
_brew_docker_plugins="${HOMEBREW_PREFIX}/lib/docker/cli-plugins"
readonly MYDIR _brew_docker_plugins
#
##############################################################################
#
# Load some utilities
#
readonly THE_UTILS=( "common" )

for utility in "${THE_UTILS[@]}"; do
  if [[ -r "${MYDIR}/utils_${utility}.sh" ]]; then
    source "${MYDIR}/utils_${utility}.sh"
  else
    >&2 echo -e "\e[31mERROR:\e[39m Unable to load library 'utils_${utility}.sh'"
    exit 1
  fi
done


if [[ "$(uname -s)" != "Darwin" ]]; then
    puterr "I'm sorry, this doesn't seem like macOS. Exiting..."
    exit 7
fi

putinfo "Checking colima and deps..."
_missing="$(brew info colima lima docker docker-compose docker-buildx jq | grep -c '^Not installed')"
echo -e "${_FG_GREEN}done${_FG_NORMAL}"

if [[ $_missing -gt 0 ]]; then
    putinfo "Installing Colima and deps..."
    brew install colima lima docker docker-compose docker-buildx jq
    echo -e "${_FG_GREEN}done${_FG_NORMAL}"
else
    putsuccess "All deps are installed"
fi

if [[ $(grep -c "${_brew_docker_plugins}" ~/.docker/config.json) -eq 0 ]]; then
    _backup_time="$(date '+%F_%T')"
    putinfo "Configuring Docker CLI plugin paths..."
    cp ~/.docker/config.json "${HOME}/.docker/config.json-${_backup_time}"
    jq 'if .cliPluginsExtraDirs then .cliPluginsExtraDirs[] += "'"${_brew_docker_plugins}"'" else .cliPluginsExtraDirs = ["'"${_brew_docker_plugins}"'"] end' \
        < ~/.docker/"config.json-${_backup_time}" > ~/.docker/config.json
    putsuccess "Docker CLI plugin paths configured"
fi

set -x
_docker_desktop_installed="$(brew info docker-desktop | grep -c '^Not installed')"
_creds_configured="$(grep -c "credsStore" ~/.docker/config.json)"

# Lingering Docker Desktop credsStore will confuse colima
if [[ $_docker_desktop_installed -eq 1 && $_creds_configured -eq 1 ]]; then
    _backup_time="$(date '+%F_%T')"
    putinfo "Removing Docker desktop credstore config..."
        cp ~/.docker/config.json "${HOME}/.docker/config.json-${_backup_time}"
    jq 'if .credsStore then del(.credsStore) end' < ~/.docker/"config.json-${_backup_time}" > ~/.docker/config.json
    putsuccess "Docker desktop credstore config removed"
fi
