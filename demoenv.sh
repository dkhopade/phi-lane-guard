#!/usr/bin/env bash
# Source this in any new shell before demoing:  source demoenv.sh
# Loads the current build's IPs and key so you never hand-type them.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
source "$HERE/infra/.state"
export GATEWAY="http://$GW_IP:4000"
export LITELLM_MASTER_KEY=$(ssh -o BatchMode=yes -o ConnectTimeout=8 \
  opc@"$GW_IP" 'grep LITELLM_MASTER_KEY /opt/phi/src/gateway/.env 2>/dev/null || grep LITELLM_MASTER_KEY ~/phi-gateway/.env 2>/dev/null' \
  | cut -d= -f2)
echo "gateway : $GATEWAY"
echo "model   : ${MODEL_FQDN:-$GPU_IP}"
echo "key     : ${LITELLM_MASTER_KEY:0:12}…"
