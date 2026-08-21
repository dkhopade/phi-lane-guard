#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Poll both hosts until they finish self-configuring.
#
#   ./status.sh           one check
#   ./status.sh --watch   re-check every 30s until both are ready
#
# Reads the readiness markers written by cloud-init, so it reports what the
# hosts actually achieved rather than whether they merely booted.
# ---------------------------------------------------------------------------
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/.state"

SSHO="-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8"

check() {
  local gw="pending" md="pending" gwdetail="" mddetail=""

  if ssh $SSHO opc@"$GW_IP" 'test -f /var/tmp/phi-gateway-ready' 2>/dev/null; then
    gw="READY"
  elif ssh $SSHO opc@"$GW_IP" 'test -f /var/log/phi-bootstrap.log' 2>/dev/null; then
    gwdetail=$(ssh $SSHO opc@"$GW_IP" 'grep -- "---" /var/log/phi-bootstrap.log | tail -1' 2>/dev/null)
  else
    gwdetail="booting / cloud-init not started"
  fi

  if ssh $SSHO -J opc@"$GW_IP" opc@"$GPU_IP" 'test -f /var/tmp/phi-model-ready' 2>/dev/null; then
    md="READY"
  elif ssh $SSHO -J opc@"$GW_IP" opc@"$GPU_IP" 'test -f /var/log/phi-bootstrap.log' 2>/dev/null; then
    mddetail=$(ssh $SSHO -J opc@"$GW_IP" opc@"$GPU_IP" 'grep -- "---" /var/log/phi-bootstrap.log | tail -1' 2>/dev/null)
  else
    mddetail="booting / cloud-init not started"
  fi

  printf '\n[%s]\n' "$(date -u +%H:%M:%SZ)"
  printf '  gateway  %-8s %s\n' "$gw" "$gwdetail"
  printf '  model    %-8s %s\n' "$md" "$mddetail"

  if [ "$gw" = "READY" ] && [ "$md" = "READY" ]; then
    cat <<DONE

  -------------------------------------------------------------
  Both hosts ready. Next:

      ./03-seal-subnet.sh seal
      cd ../demo && source ../demoenv.sh && python scenarios.py
  -------------------------------------------------------------
DONE
    return 0
  fi
  return 1
}

if [ "${1:-}" = "--watch" ]; then
  while ! check; do sleep 30; done
else
  check || echo "
  Not ready yet. Re-run, or use --watch. Logs:
    ssh opc@$GW_IP 'tail -20 /var/log/phi-bootstrap.log'
    ssh -J opc@$GW_IP opc@$GPU_IP 'tail -20 /var/log/phi-bootstrap.log'"
fi
