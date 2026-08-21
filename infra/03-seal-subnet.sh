#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 03 - Seal the private subnet. THIS IS THE DEMO.
#
# Swaps the private subnet's route table from rt-private-build (NAT) to
# rt-private-sealed (no routes). After this, the GPU host has no path to
# the internet by any protocol. The gateway still reaches it over the VCN.
#
# Usage:  ./03-seal-subnet.sh seal    | unseal
# ---------------------------------------------------------------------------
set -euo pipefail
STATE="$(dirname "$0")/.state"; source "$STATE"
MODE="${1:-seal}"

case "$MODE" in
  seal)   RT="$RT_SEALED"; MSG="SEALED — no internet route" ;;
  unseal) RT="$RT_BUILD";  MSG="BUILD — NAT egress restored" ;;
  *) echo "usage: $0 [seal|unseal]"; exit 1 ;;
esac

oci network subnet update --subnet-id "$SUBNET_PRIV" \
  --route-table-id "$RT" --force >/dev/null

echo "Private subnet is now: $MSG"
echo
echo "Verify from the model host (should hang, then fail):"
echo "  ssh -J opc@$GW_IP opc@$GPU_IP 'curl -m 8 -sS https://api.anthropic.com || echo BLOCKED'"
