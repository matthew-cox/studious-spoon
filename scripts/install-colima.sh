#!/usr/bin/env bash
#
# Small script to attempt and get colima setup from scratch
#
_brew_docker_plugins="${HOMEBREW_PREFIX}/lib/docker/cli-plugins"
readonly _brew_docker_plugins

_FG_RED="$(echo -e 'bold\nsetaf 1' | tput -S)"
_FG_GREEN="$(echo -e 'bold\nsetaf 2' | tput -S)"
_FG_YELLOW="$(echo -e 'bold\nsetaf 3' | tput -S)"
_FG_BLUE="$(echo -e 'bold\nsetaf 4' | tput -S)"
_FG_PINK="$(echo -e 'bold\nsetaf 5' | tput -S)"
_FG_TEAL="$(echo -e 'bold\nsetaf 6' | tput -S)"
_FG_NORMAL="$(tput sgr0)"

readonly _FG_RED _FG_GREEN _FG_YELLOW _FG_BLUE _FG_PINK _FG_TEAL _FG_NORMAL

if [[ "$(uname -s)" != "Darwin" ]]; then
    >&2 echo -e "${_FG_RED}ERROR:${_FG_NORMAL} I'm sorry, this doesn't seem like macOS. Exiting..."
    exit 7
fi

echo -ne "${_FG_TEAL}NOTE:${_FG_NORMAL} Checking colima and deps..."
_missing="$(brew info colima lima docker docker-compose docker-buildx jq | grep -c '^Not installed')"
echo -e "${_FG_GREEN}done${_FG_NORMAL}"

if [[ $_missing -gt 0 ]]; then
    echo -e "${_FG_TEAL}NOTE:${_FG_NORMAL} Installing Colima and deps..."
    brew install colima lima docker docker-compose docker-buildx jq
    echo -e "${_FG_GREEN}done${_FG_NORMAL}"
else
    echo -e "${_FG_GREEN}DONE:${_FG_NORMAL} All deps are installed"
fi

if [[ $(grep -c "${_brew_docker_plugins}" ~/.docker/config.json) -eq 0 ]]; then
    _backup_time="$(date '+%F_%T')"
    echo -ne "${_FG_TEAL}NOTE:${_FG_NORMAL}Configuring Docker CLI plugin paths..."
    cp ~/.docker/config.json "${HOME}/.docker/config.json-${_backup_time}"
    jq 'if .cliPluginsExtraDirs then .cliPluginsExtraDirs[] += "'"${_brew_docker_plugins}"'" else .cliPluginsExtraDirs = ["'"${_brew_docker_plugins}"'"] end' \
        < ~/.docker/"config.json-${_backup_time}" > ~/.docker/config.json
    echo -e "${_FG_GREEN}done${_FG_NORMAL}"
fi

set -x
_docker_desktop_installed="$(brew info docker-desktop | grep -c '^Not installed')"
_creds_configured="$(grep -c "credsStore" ~/.docker/config.json)"

# Lingering Docker Desktop credsStore will confuse colima
if [[ $_docker_desktop_installed -eq 1 && $_creds_configured -eq 1 ]]; then
    _backup_time="$(date '+%F_%T')"
    echo -ne "${_FG_TEAL}NOTE:${_FG_NORMAL} Removing Docker desktop credstore config..."
        cp ~/.docker/config.json "${HOME}/.docker/config.json-${_backup_time}"
    jq 'if .credsStore then del(.credsStore) end' < ~/.docker/"config.json-${_backup_time}" > ~/.docker/config.json
    echo -e "${_FG_GREEN}done${_FG_NORMAL}"
fi
