#!/usr/bin/env bash
# Source this in any new shell before demoing:  source demoenv.sh
#
# Loads the current build's gateway URL and master key so you never hand-type
# them. Handles both provisioning paths:
#   cloud-init build -> /opt/phi/src/gateway/.env  (root-owned, chmod 600)
#   manual build     -> ~/phi-gateway/.env
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
source "$HERE/infra/.state"

export GATEWAY="http://$GW_IP:4000"

# .env is chmod 600 and root-owned under cloud-init, so this needs sudo. It is
# passwordless for opc on OCI images.
export LITELLM_MASTER_KEY=$(ssh -o BatchMode=yes -o ConnectTimeout=10 opc@"$GW_IP" \
  'sudo grep -h LITELLM_MASTER_KEY /opt/phi/src/gateway/.env ~/phi-gateway/.env 2>/dev/null | head -1' \
  | cut -d= -f2 | tr -d '\r\n')

# Activate the demo venv if one is present next to the repo
for v in "$HERE/.venv" "$HERE/../.venv" "$HERE/../../phi-lane-poc/.venv"; do
  [ -f "$v/bin/activate" ] && . "$v/bin/activate" && break
done

echo "gateway : $GATEWAY"
echo "model   : ${MODEL_FQDN:-$GPU_IP}"
if [ -n "$LITELLM_MASTER_KEY" ]; then
  echo "key     : ${LITELLM_MASTER_KEY:0:10}… (loaded)"
else
  echo "key     : NOT FOUND — set it by hand:"
  echo "          export LITELLM_MASTER_KEY=<the value you passed to 02-instances.sh>"
fi
python -c "import httpx,rich" 2>/dev/null || echo "note   : pip install httpx rich"
