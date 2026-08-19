#!/usr/bin/env bash
# Prove the page answers on the LAN and nothing else is exposed (FR-023, FR-026).
#
#   ./ops/verify-lan-only.sh <mac-mini-lan-ip> [port] [stack-name]
#
# The parts a script can check are checked. The last question — whether your router
# forwards the port from outside — a script on this host cannot answer, so it asks you.
set -uo pipefail

LAN_IP="${1:-}"
PORT="${2:-8080}"
STACK="${3:-pdf2md}"
failures=0

if [ -z "$LAN_IP" ]; then
  echo "usage: $0 <mac-mini-lan-ip> [port] [stack-name]" >&2
  echo "hint: ipconfig getifaddr en0" >&2
  exit 2
fi

container_for() {
  docker ps -q \
    --filter "label=com.docker.compose.project=${STACK}" \
    --filter "label=com.docker.compose.service=$1" | head -n1
}

echo "== the page must answer on the LAN address =="
code="$(curl -s -o /dev/null -m 10 -w '%{http_code}' "http://${LAN_IP}:${PORT}/")"
if [ "$code" = "200" ]; then
  echo "  ok    http://${LAN_IP}:${PORT}/ → 200"
else
  echo "  FAIL  http://${LAN_IP}:${PORT}/ → ${code}"
  failures=$((failures + 1))
fi

echo
echo "== only the web service may publish a port =="
for service in web docling; do
  container="$(container_for "$service")"
  if [ -z "$container" ]; then
    echo "  FAIL  service '${service}' is not running in stack '${STACK}'"
    failures=$((failures + 1))
    continue
  fi
  ports="$(docker inspect -f '{{json .NetworkSettings.Ports}}' "$container")"
  bindings="$(echo "$ports" | grep -o 'HostPort' | wc -l | tr -d ' ')"
  if [ "$service" = "docling" ]; then
    if [ "$bindings" = "0" ]; then
      echo "  ok    engine publishes no ports"
    else
      echo "  FAIL  engine publishes ports: ${ports}"
      failures=$((failures + 1))
    fi
  else
    echo "  ok    web publishes: ${ports}"
  fi
done

echo
echo "== the API answers, and needs no credential =="
health="$(curl -s -m 10 "http://${LAN_IP}:${PORT}/api/health")"
if echo "$health" | grep -q '"status"'; then
  echo "  ok    /api/health answers an anonymous request"
else
  echo "  FAIL  /api/health did not answer: ${health}"
  failures=$((failures + 1))
fi

cat <<'MSG'

== what this host cannot check for you ==

  1. From a device OUTSIDE this network (a phone on cellular, with Wi-Fi off), the
     address must be unreachable.
  2. No router port-forward, DMZ entry, or reverse proxy may map this port to the
     internet.
  3. No tunnelling service (ngrok, Cloudflare Tunnel, Tailscale Funnel) may be
     exposing it.

MSG

printf "Have you confirmed all three? [y/N] "
read -r answer
case "$answer" in
  y | Y | yes | YES) echo "  ok    operator confirmed" ;;
  *)
    echo "  FAIL  not confirmed — the LAN-only property is unverified"
    failures=$((failures + 1))
    ;;
esac

echo
if [ "$failures" -eq 0 ]; then
  echo "PASS — reachable on the LAN, nothing else published, nothing exposed outside."
  exit 0
fi
echo "FAIL — ${failures} check(s) failed."
exit 1
