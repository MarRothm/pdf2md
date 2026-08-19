#!/usr/bin/env bash
# Prove the deployed stack cannot reach the internet, and that the web service can
# still reach the engine (FR-021, FR-022, FR-026).
#
#   ./ops/verify-offline.sh [stack-name]        default stack name: pdf2md
#
# Run it after every deploy that touched `networks:` — that block is the security
# posture of this stack, and nothing else enforces it.
set -uo pipefail

STACK="${1:-pdf2md}"
PROBE_HOST="${PROBE_HOST:-1.1.1.1}"
TIMEOUT="${TIMEOUT:-5}"
failures=0

container_for() {
  docker ps -q \
    --filter "label=com.docker.compose.project=${STACK}" \
    --filter "label=com.docker.compose.service=$1" | head -n1
}

WEB="$(container_for web)"
DOCLING="$(container_for docling)"

if [ -z "$WEB" ] || [ -z "$DOCLING" ]; then
  echo "could not find both services of stack '${STACK}' — is it running?" >&2
  echo "usage: $0 [stack-name]" >&2
  exit 2
fi

# A TCP connect attempt, using python3 because neither image is guaranteed curl.
egress_probe() {
  docker exec "$1" python3 -c "
import socket, sys
try:
    socket.create_connection(('${PROBE_HOST}', 80), timeout=${TIMEOUT}).close()
except OSError as error:
    print(f'blocked: {error}')
    sys.exit(1)
print('REACHED THE INTERNET')
sys.exit(0)
" 2>&1
}

check_blocked() {
  local name="$1" container="$2" output
  output="$(egress_probe "$container")"
  if [ $? -eq 0 ]; then
    echo "  FAIL  ${name} reached ${PROBE_HOST} — this stack is not isolated"
    echo "        ${output}"
    failures=$((failures + 1))
  else
    echo "  ok    ${name} egress blocked (${output})"
  fi
}

echo "== egress must fail from both services =="
check_blocked "engine" "$DOCLING"
check_blocked "web   " "$WEB"

echo
echo "== the web service must still reach the engine over the internal network =="
if docker exec "$WEB" python3 -c "
import os, sys, urllib.request
request = urllib.request.Request('http://docling:5001/health')
request.add_header('X-Api-Key', os.environ.get('PDF2MD_ENGINE_API_KEY', ''))
sys.exit(0 if urllib.request.urlopen(request, timeout=${TIMEOUT}).status == 200 else 1)
" >/dev/null 2>&1; then
  echo "  ok    web → docling:5001 reachable by service name"
else
  echo "  FAIL  web cannot reach docling:5001 — conversions will not work"
  failures=$((failures + 1))
fi

echo
echo "== the engine must not be publishing any port =="
published="$(docker inspect -f '{{json .NetworkSettings.Ports}}' "$DOCLING")"
if [ "$published" = "{}" ] || echo "$published" | grep -q '":null'; then
  echo "  ok    engine publishes no ports"
else
  echo "  FAIL  engine publishes ports: ${published}"
  failures=$((failures + 1))
fi

echo
echo "== the engine log must show no download attempts =="
if docker logs "$DOCLING" 2>&1 | grep -iE "download|huggingface|resolve failed|name resolution" | head -5 | grep -q .; then
  echo "  WARN  the engine log mentions downloads — inspect it:"
  docker logs "$DOCLING" 2>&1 | grep -iE "download|huggingface|resolve failed|name resolution" | head -5 | sed 's/^/        /'
  echo "        A working offline deploy converts documents without any of these."
  failures=$((failures + 1))
else
  echo "  ok    no download attempts in the engine log"
fi

echo
if [ "$failures" -eq 0 ]; then
  echo "PASS — the stack has no path to the internet, and the engine is reachable only"
  echo "       from the web service. Convert a document now to confirm end to end."
  exit 0
fi
echo "FAIL — ${failures} check(s) failed. Do not treat this deployment as isolated."
exit 1
