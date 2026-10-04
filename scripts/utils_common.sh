#!/usr/bin/env bash
# shellcheck disable=SC1117,SC2155
#
# common message functions
#
# set -o errexit -o pipefail -o nounset
#
##############################################################################
#
# look for old busted BSD echo

if [[ -z "${_old_echo+x}" ]]; then
  # save errexit from $- ("$(set +o)" can't: bash turns errexit off inside command substitution,
  # so restoring from it would switch errexit off for the calling script)
  _common_errexit=0
  if [[ $- == *e* ]]; then _common_errexit=1; fi
  set +o errexit
  _old_echo=$(echo -e "\e[31m" | grep -c '\\e\[31m')
  readonly _old_echo
  # restore the settings
  if [[ $_common_errexit -eq 1 ]]; then set -o errexit; fi
fi

#
##############################################################################
#
# Foreground color sequences
#
if [[ -z "${_FG_RED+undefined}" ]]; then
  _FG_RED="$(echo -e 'bold\nsetaf 1' | tput -S)"
  _FG_GREEN="$(echo -e 'bold\nsetaf 2' | tput -S)"
  _FG_YELLOW="$(echo -e 'bold\nsetaf 3' | tput -S)"
  _FG_BLUE="$(echo -e 'bold\nsetaf 4' | tput -S)"
  _FG_PINK="$(echo -e 'bold\nsetaf 5' | tput -S)"
  _FG_TEAL="$(echo -e 'bold\nsetaf 6' | tput -S)"
  _FG_WHITE="$(echo -e 'bold\nsetaf 7' | tput -S)"
  _FG_GREY="$(echo -e 'bold\nsetaf 8' | tput -S)"
  _FG_ORANGE="$(echo -e 'bold\nsetaf 9' | tput -S)"
  _FG_NORMAL="$(tput sgr0)"
fi
readonly _FG_RED _FG_GREEN _FG_YELLOW _FG_BLUE _FG_PINK _FG_TEAL _FG_WHITE _FG_GREY _FG_ORANGE _FG_NORMAL
export  _FG_RED _FG_GREEN _FG_YELLOW _FG_BLUE _FG_PINK _FG_TEAL _FG_WHITE _FG_GREY _FG_ORANGE _FG_NORMAL

#
##############################################################################
#
# Message to STDOUT
#

_put_stdout() {
  msg="$1"
  before="$2"

  if [[ $_old_echo -eq 1 ]]; then
    printf "%s %s\n" "${before}" "$msg"
  else
    echo -e "${before} $msg"
  fi

}
#
##############################################################################
#
# Message to STDERR
#

_put_stderr() {
  msg="$1"
  before="$2"

  if [[ $_old_echo -eq 1 ]]; then
    >&2 printf "%s %s\n" "${before}" "${msg}"
  else
    >&2 echo -e "${before} $msg"
  fi
}

#
##############################################################################
#
# ERROR message to STDERR
#

puterr() {
  local _before="${_FG_RED}ERROR:${_FG_NORMAL}"
  _put_stderr "$1" "${_before}"
}

#
##############################################################################
#
# INFO message to STDOUT
#
putinfo() {
  local _before="${_FG_TEAL}NOTE:${_FG_NORMAL}"
  _put_stdout "$1" "${_before}"
}

#
##############################################################################
#
# SUCCESS message to STDOUT
#
putsuccess() {
  local _before="${_FG_GREEN}SUCCESS:${_FG_NORMAL}"
  _put_stdout "$1" "${_before}"
}

#
##############################################################################
#
# Warning message to STDERR
#
putwarn() {
  local _before="${_FG_YELLOW}WARNING:${_FG_NORMAL}"
  _put_stderr "$1" "${_before}"
}

#
##############################################################################
#
# Pause the interactive flow for user confirmation
#
pause() {
    msg="${1:-Press return to continue}"
    >&2 echo -en "${_FG_PINK}${msg}${_FG_NORMAL}"
    read -r
}

export -f puterr putinfo putsuccess putwarn pause
