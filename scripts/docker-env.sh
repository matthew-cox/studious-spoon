# Source me (don't execute): `. scripts/docker-env.sh`
# Exports DOCKER_HOST from the active Docker context and, for colima, the testcontainers
# socket override for colima and Docker Desktop (Ryuk must mount the VM's /var/run/docker.sock,
# not the host path).
# Anything you've already exported wins. The Makefile sources this for test, check, and e2e.

if [ -z "${DOCKER_HOST:-}" ]; then
  _docker_env_host="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null || true)"
  if [ -n "$_docker_env_host" ]; then
    export DOCKER_HOST="$_docker_env_host"
  fi
fi

case "${DOCKER_HOST:-}" in
  */.colima/*)
    export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE="${TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE:-/var/run/docker.sock}"
    _docker_env_runtime="colima" ;;
  *docker.raw.sock* | */.docker/run/*)
    export TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE="${TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE:-/var/run/docker.sock}"
    _docker_env_runtime="Docker Desktop" ;;
  *orbstack*) _docker_env_runtime="OrbStack" ;;
  "") _docker_env_runtime="default socket (no Docker context found)" ;;
  *) _docker_env_runtime="other" ;;
esac
echo "docker-env: ${_docker_env_runtime} (${DOCKER_HOST:-unix:///var/run/docker.sock})" >&2
unset _docker_env_host _docker_env_runtime
